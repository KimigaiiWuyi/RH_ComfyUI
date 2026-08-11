"""ChannelExtensionRegistry — 外部插件为既有模型追加通道的通用扩展点

用途:一个逻辑模型(如 seedance2)可能由多家供应商提供服务;开源仓库只声明
内置通道,外部插件在自己的 ``@on_core_start`` 里把额外通道追加进来:

    from RH_ComfyUI.core import channel_registry
    channel_registry.register_binding("seedance2", my_channel, vendor_model="xxx")

模型的 ``channel_bindings()`` 把 ``channel_registry.bindings_for(self.name)``
并入自己的候选列表,即自动参与通用 LoadBalancer 的排序 / 熔断 / 故障切换。
开源仓库对外部通道零感知:只认 ``ProviderChannel`` 抽象契约。
"""

from __future__ import annotations

import threading
from typing import Optional

from gsuid_core.logger import logger

from .channel import ChannelBinding, ProviderChannel


class ChannelExtensionRegistry:
    """模型名 → 外部追加通道绑定;线程安全,进程级单例。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bindings: dict[str, list[ChannelBinding]] = {}

    def register_binding(
        self,
        model_name: str,
        channel: ProviderChannel,
        *,
        vendor_model: Optional[str] = None,
    ) -> None:
        """为 ``model_name`` 追加一个通道绑定(同名通道后注册者覆盖)。"""
        binding = ChannelBinding(channel=channel, vendor_model=vendor_model)
        with self._lock:
            existing = self._bindings.setdefault(model_name, [])
            # 同名通道去重:后注册覆盖(便于外部插件热替换自己的通道)
            existing[:] = [b for b in existing if b.channel.name != channel.name]
            existing.append(binding)
        logger.info(
            f"[ChannelRegistry] 为模型 {model_name} 追加通道 {channel.name} (vendor_model={vendor_model or '-'})"
        )

    def bindings_for(self, model_name: str) -> list[ChannelBinding]:
        """返回 ``model_name`` 上已注册的外部通道绑定(只读快照)。"""
        with self._lock:
            return list(self._bindings.get(model_name, []))

    def unregister(self, model_name: str, channel_name: str) -> None:
        with self._lock:
            existing = self._bindings.get(model_name)
            if existing:
                existing[:] = [b for b in existing if b.channel.name != channel_name]

    def clear(self) -> None:
        """清空(仅供测试用)。"""
        with self._lock:
            self._bindings.clear()


channel_registry = ChannelExtensionRegistry()


__all__ = ["ChannelExtensionRegistry", "channel_registry"]
