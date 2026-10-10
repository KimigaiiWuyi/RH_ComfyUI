"""启动时给历史任务补凭证前 6 位。

导出读的是 rhcomfyuitaskrecord.backend_key_prefix。旧行当时没写,
这里用当前配置补空值。已经记过的不改;供应商名对不上的不猜。
只补一遍,并且按主键翻页提交。一次扫全表会占住 SQLite 写锁,
同一时刻宿主也在写同一库,就会报 database is locked。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from gsuid_core.logger import logger


def prefix_for_row(
    backend: str,
    provider: str,
    prefixes: dict[str, str],
    vendor_channel: str = "",
) -> str:
    """供应商名优先。供应商是别的通道时,不用本后端的 key 去填。

    供应商列为空时,再用请求里记下的 vendor_channel 对当前钥匙。
    """
    provider_name = provider.strip()
    backend_name = backend.strip()
    if provider_name in prefixes:
        return prefixes[provider_name]
    if provider_name not in ("", backend_name):
        return ""
    channel = vendor_channel.strip()
    if channel in prefixes:
        return prefixes[channel]
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


_PAGE_SIZE = 200


def _marker_path() -> Path:
    from ..resource.RESOURCE_PATH import MAIN_PATH

    return MAIN_PATH / "key_prefix_backfill.done"


async def backfill_historical_key_prefixes() -> int:
    """补空前缀。返回写上的行数。已经补过,或当前没有凭证,则直接返回。"""
    from .models import RHComfyuiTaskRecord

    marker = _marker_path()
    if marker.is_file():
        return 0
    prefixes = collect_audit_prefixes()
    if not prefixes:
        return 0
    filled = 0
    after_id = 0
    while True:
        page = await RHComfyuiTaskRecord.list_blank_key_prefix_page(after_id=after_id, limit=_PAGE_SIZE)
        if not page:
            break
        updates: dict[int, str] = {}
        for row_id, backend, provider, vendor_channel in page:
            after_id = row_id
            prefix = prefix_for_row(backend, provider, prefixes, vendor_channel)
            if prefix:
                updates[row_id] = prefix
        if updates:
            filled += await RHComfyuiTaskRecord.apply_key_prefix_batch(updates)
        await asyncio.sleep(0)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done", encoding="utf-8")
    if filled:
        logger.info(f"[RHComfyUI] 已为 {filled} 条历史任务补上凭证前缀")
    return filled


def _log_backfill_task(done: asyncio.Task[int]) -> None:
    if done.cancelled():
        return
    exc = done.exception()
    if exc is not None:
        logger.warning(f"[RHComfyUI] 历史凭证前缀补写失败: {exc}")


def schedule_key_prefix_backfill() -> None:
    """丢到后台。启动钩子里同步扫表会和宿主的写入抢同一把写锁。"""
    task = asyncio.create_task(backfill_historical_key_prefixes(), name="rh-key-prefix-backfill")
    task.add_done_callback(_log_backfill_task)


__all__ = [
    "prefix_for_row",
    "collect_audit_prefixes",
    "backfill_historical_key_prefixes",
    "schedule_key_prefix_backfill",
]
