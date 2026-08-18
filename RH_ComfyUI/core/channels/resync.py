"""通道绑定重挂扩展点 — 配置改完把 channel_registry 按当前快照重绑

凭证(api_key / base_url / Enable)由通道 ``check_available()`` 热读,改完即生效。
**绑定关系**(给哪个模型挂哪条通道、vendor_model、权重)是注入时的快照:
增删供应商 / 改 Slot 模型勾选后必须重挂,否则 registry 仍是旧矩阵。

开源侧不感知外部插件。外部插件在 ``@on_core_start`` 登记钩子:

    from RH_ComfyUI.core import register_resync_hook, bind_config_resync
    register_resync_hook("my_gateway", my_register_fn)
    bind_config_resync(MY_CONFIG, frozenset({"Slot_Models"}))

``my_register_fn`` 必须幂等:用 ``sync_owned_bindings`` 按差量卸/挂,
无变化不打 info。``resync_channel_bindings()`` 由 ``rh 刷新供应商``
与被监视的 ``set_config`` 调用。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional
from dataclasses import dataclass

from gsuid_core.logger import logger

from .channel import ChannelBinding, ProviderChannel
from .registry import channel_registry

ResyncHook = Callable[[], object]
OwnedBinding = tuple[str, str]
DesiredBinding = tuple[str, ProviderChannel, Optional[str]]


@dataclass(frozen=True)
class BindingDiff:
    """一层通道绑定相对上次快照的差量。"""

    added: tuple[tuple[str, str, str], ...]
    removed: tuple[tuple[str, str, str], ...]
    changed: tuple[tuple[str, str, str, str], ...]

    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)


def _live_binding(model_name: str, channel_name: str) -> Optional[ChannelBinding]:
    for binding in channel_registry.bindings_for(model_name):
        if binding.channel.name == channel_name:
            return binding
    return None


def sync_owned_bindings(
    owned: list[OwnedBinding],
    desired: list[DesiredBinding],
) -> tuple[list[OwnedBinding], BindingDiff]:
    """按 desired 同步本层绑定:只卸不再需要的,只挂新增/vendor·权重有变的。

    ``owned`` 是本层上次记下的 (model, channel.name)。返回新的 owned 与差量。
    live registry 被清空时(测试 / 热重载),同名 desired 会按新增补回。
    """
    desired_index: dict[OwnedBinding, tuple[ProviderChannel, Optional[str]]] = {}
    for model_name, channel, vendor_model in desired:
        desired_index[(model_name, channel.name)] = (channel, vendor_model)

    removed: list[tuple[str, str, str]] = []
    for model_name, channel_name in owned:
        if (model_name, channel_name) in desired_index:
            continue
        live = _live_binding(model_name, channel_name)
        old_vendor = (live.vendor_model or "-") if live is not None else "-"
        channel_registry.unregister(model_name, channel_name)
        removed.append((model_name, channel_name, old_vendor))

    added: list[tuple[str, str, str]] = []
    changed: list[tuple[str, str, str, str]] = []
    for (model_name, channel_name), (channel, vendor_model) in desired_index.items():
        live = _live_binding(model_name, channel_name)
        if live is None:
            channel_registry.register_binding(model_name, channel, vendor_model=vendor_model)
            added.append((model_name, channel_name, vendor_model or "-"))
            continue
        if live.vendor_model != vendor_model or live.channel.weight != channel.weight:
            old_vendor = live.vendor_model or "-"
            channel_registry.register_binding(model_name, channel, vendor_model=vendor_model)
            changed.append((model_name, channel_name, old_vendor, vendor_model or "-"))

    new_owned = [(model_name, channel.name) for model_name, channel, _vendor in desired]
    return new_owned, BindingDiff(tuple(added), tuple(removed), tuple(changed))


def log_binding_diff(prefix: str, diff: BindingDiff, total: int) -> None:
    """有变化才打一条 info;无变化静默。"""
    if not diff.has_changes():
        return
    parts: list[str] = []
    if diff.added:
        parts.append("+" + ",".join(f"{m}/{c}" for m, c, _v in diff.added))
    if diff.removed:
        parts.append("-" + ",".join(f"{m}/{c}" for m, c, _v in diff.removed))
    if diff.changed:
        parts.append("~" + ",".join(f"{m}/{c}" for m, c, _o, _n in diff.changed))
    logger.info(f"{prefix}: {'; '.join(parts)} (共 {total} 条)")


class ChannelResyncRegistry:
    """进程级重绑钩子表;线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hooks: dict[str, ResyncHook] = {}
        self._watched: dict[int, tuple[Any, set[str]]] = {}
        self._running = False

    def register(self, name: str, fn: ResyncHook) -> None:
        """按 name 登记;同名后注册覆盖(便于热替换)。"""
        with self._lock:
            self._hooks[name] = fn
        logger.info(f"[ChannelResync] 已登记重绑钩子: {name}")

    def unregister(self, name: str) -> None:
        with self._lock:
            self._hooks.pop(name, None)

    def run(self) -> list[str]:
        """跑完全部钩子。单钩子失败不影响其余;返回成功跑完的 name。"""
        with self._lock:
            if self._running:
                return []
            self._running = True
            items = list(self._hooks.items())
        ok: list[str] = []
        try:
            for name, fn in items:
                try:
                    fn()
                except Exception:  # noqa: BLE001 — 外部钩子类型不可控,隔离以免拖垮其余通道
                    logger.exception(f"[ChannelResync] 钩子 {name} 失败")
                    continue
                ok.append(name)
        finally:
            with self._lock:
                self._running = False
        logger.debug(f"[ChannelResync] 重绑完成: {', '.join(ok) if ok else '(无钩子)'}")
        return ok

    def bind_config(self, cfg: Any, keys: frozenset[str]) -> None:
        """``cfg.set_config(key)`` 成功且 key 在 keys 内时自动 ``run()``。

        对同一 cfg 重复调用会把 keys 并入已监视集合,不会叠多层包装。
        """
        ident = id(cfg)
        with self._lock:
            existing = self._watched.get(ident)
            if existing is not None:
                existing[1].update(keys)
                return
            keys_set: set[str] = set(keys)
            original = cfg.set_config
            self._watched[ident] = (original, keys_set)

        def wrapped(key: str, value: Any) -> bool:
            ok = original(key, value)
            if ok and key in keys_set:
                self.run()
            return ok

        cfg.set_config = wrapped

    def clear(self) -> None:
        """清空钩子(仅供测试)。不拆已包装的 set_config。"""
        with self._lock:
            self._hooks.clear()


channel_resync = ChannelResyncRegistry()


def register_resync_hook(name: str, fn: ResyncHook) -> None:
    channel_resync.register(name, fn)


def resync_channel_bindings() -> list[str]:
    """按当前配置重挂全部已登记通道绑定。"""
    return channel_resync.run()


def bind_config_resync(cfg: Any, keys: frozenset[str]) -> None:
    """监视 StringConfig.set_config:命中 keys 则重绑。"""
    channel_resync.bind_config(cfg, keys)


__all__ = [
    "ResyncHook",
    "BindingDiff",
    "ChannelResyncRegistry",
    "channel_resync",
    "register_resync_hook",
    "resync_channel_bindings",
    "bind_config_resync",
    "sync_owned_bindings",
    "log_binding_diff",
]
