"""启动时给历史任务补凭证前 6 位。

导出读的是 rhcomfyuitaskrecord.backend_key_prefix。旧行当时没写,
这里用当前配置补空值。已经记过的不改;供应商名对不上的不猜。
"""

from __future__ import annotations

from gsuid_core.logger import logger


def prefix_for_row(backend: str, provider: str, prefixes: dict[str, str]) -> str:
    """供应商名优先。供应商是别的通道时,不用本后端的 key 去填。"""
    provider_name = provider.strip()
    backend_name = backend.strip()
    if provider_name in prefixes:
        return prefixes[provider_name]
    if provider_name not in ("", backend_name):
        return ""
    if backend_name in prefixes:
        return prefixes[backend_name]
    return ""


def collect_audit_prefixes() -> dict[str, str]:
    """已注册后端和模型通道各自的当前凭证前 6 位。"""
    from ..backends import backend_registry
    from ...core.routing.registry import model_registry

    found: dict[str, str] = {}

    def put(name: str, prefix: str) -> None:
        key = name.strip()
        text = prefix.strip()[:6]
        if key and text:
            found[key] = text

    for adapter in backend_registry.all():
        put(adapter.name, adapter.audit_key_prefix())
    for model in model_registry.all_models():
        try:
            bindings = model.channel_bindings()
        except Exception as exc:  # noqa: BLE001 — 单个模型通道读失败不挡住其余补写
            logger.warning(f"[RHComfyUI] 读取 {model.name} 通道凭证失败,跳过: {exc}")
            continue
        for binding in bindings:
            for name, prefix in binding.channel.audit_prefix_names().items():
                put(name, prefix)
    return found


async def backfill_historical_key_prefixes() -> int:
    """补空前缀。返回写上的行数。"""
    from .models import RHComfyuiTaskRecord

    prefixes = collect_audit_prefixes()
    if not prefixes:
        return 0
    filled = await RHComfyuiTaskRecord.backfill_empty_key_prefixes(prefixes)
    if filled:
        logger.info(f"[RHComfyUI] 已为 {filled} 条历史任务补上凭证前缀")
    return filled


__all__ = [
    "prefix_for_row",
    "collect_audit_prefixes",
    "backfill_historical_key_prefixes",
]
