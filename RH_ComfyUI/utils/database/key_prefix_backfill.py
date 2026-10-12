"""启动时把历史任务的凭证补成前5*****后5。

导出读的是 rhcomfyuitaskrecord.backend_key_prefix。旧行当时只写了前 6 位,
这里用当前配置把旧前缀写成脱敏钥。已经是新掩码的不改;供应商名对不上的不猜。
空串是无钥终态,只在首次扫描(无 remaining 标记)时尝试补齐,之后不再计入 COUNT。
先 COUNT 旧前 6 位,磁盘 ``remaining=N`` 且 COUNT≤N 则跳过翻页。
一次扫全表会占住 SQLite 写锁,同一时刻宿主也在写同一库,就会报 database is locked。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from gsuid_core.logger import logger

from ..audit_key import AUDIT_KEY_STARS, AUDIT_KEY_STORE_MAX

# 与 RHComfyuiTaskRecord.backend_provider max_length 一致。更长的通道名落库会被截断。
PROVIDER_STORE_MAX = 32
_GATEWAY_SLOT_PREFIX = "gateway_slot"
_RH_KEY_NAMES = frozenset({"comfyui", "runninghub", "rh_app"})


def _gateway_slot_stem(name: str) -> str:
    """``gateway_slot2_gpt_image_2`` → ``gateway_slot2``;对不上则空。"""
    text = name.strip()
    if not text.startswith(_GATEWAY_SLOT_PREFIX):
        return ""
    rest = text[len(_GATEWAY_SLOT_PREFIX) :]
    digits = 0
    while digits < len(rest) and rest[digits].isdigit():
        digits += 1
    if digits == 0:
        return ""
    return f"{_GATEWAY_SLOT_PREFIX}{rest[:digits]}"


def _prefer_slot_value(values: set[str]) -> str:
    """同一槽位可能同时有前6位和前5*****后5,优先完整脱敏。"""
    found = {item.strip() for item in values if item.strip()}
    if not found:
        return ""
    if len(found) == 1:
        return next(iter(found))
    masked = {item for item in found if AUDIT_KEY_STARS in item}
    if len(masked) == 1:
        return next(iter(masked))
    return ""


def _prefix_for_name(name: str, prefixes: dict[str, str]) -> str:
    """精确名,或 VARCHAR(32) 截断后只对得上一个现网通道时,用那把活钥匙。"""
    key = name.strip()
    if not key:
        return ""
    if key in prefixes:
        return prefixes[key]
    if len(key) != PROVIDER_STORE_MAX:
        return ""
    hits = [full for full in prefixes if len(full) > PROVIDER_STORE_MAX and full.startswith(key)]
    if len(hits) != 1:
        return ""
    return prefixes[hits[0]]


def _prefix_for_slot(name: str, prefixes: dict[str, str]) -> str:
    """``gateway_slotN_*`` 同一槽共用一把钥;未再注册的同槽通道名仍用该槽现网钥。"""
    stem = _gateway_slot_stem(name)
    if not stem:
        return ""
    if stem in prefixes:
        return prefixes[stem]
    values = {prefixes[key] for key in prefixes if key == stem or key.startswith(f"{stem}_")}
    return _prefer_slot_value(values)


def _prefix_for_rh_key(backend: str, provider: str, channel: str, prefixes: dict[str, str]) -> str:
    """comfyui / runninghub / rh_app 共用 RH_apikey,不看 Seedance RunningHub 开关。"""
    if {backend, provider, channel}.isdisjoint(_RH_KEY_NAMES):
        return ""
    return (
        _prefix_for_name("rh_app", prefixes)
        or _prefix_for_name("comfyui", prefixes)
        or _prefix_for_name("runninghub", prefixes)
    )


def prefix_for_row(
    backend: str,
    provider: str,
    prefixes: dict[str, str],
    vendor_channel: str = "",
) -> str:
    """供应商名优先。供应商是别的通道时,不用本后端的 key 去填。

    供应商列为空时,再用请求里记下的 vendor_channel 对当前钥匙。
    列宽截断后的供应商名仍视为同一通道:能唯一对上现网全名,或 extra 里的
    vendor_channel 是该截断名的补全,就用现网钥。
    ``gateway_slotN_*`` 用该槽已注册通道的现网钥。
    comfyui / runninghub 与 rh_app 共用 RH_apikey。
    """
    provider_name = provider.strip()
    backend_name = backend.strip()
    found = _prefix_for_name(provider_name, prefixes) or _prefix_for_slot(provider_name, prefixes)
    if found:
        return found
    channel = vendor_channel.strip()
    if provider_name not in ("", backend_name):
        if len(provider_name) == PROVIDER_STORE_MAX and channel.startswith(provider_name):
            found = _prefix_for_name(channel, prefixes) or _prefix_for_slot(channel, prefixes)
            if found:
                return found
        return _prefix_for_rh_key(backend_name, provider_name, channel, prefixes)
    found = _prefix_for_name(channel, prefixes) or _prefix_for_slot(channel, prefixes)
    if found:
        return found
    found = _prefix_for_name(backend_name, prefixes)
    if found:
        return found
    return _prefix_for_rh_key(backend_name, provider_name, channel, prefixes)


def collect_audit_prefixes() -> dict[str, str]:
    """已注册后端和模型通道各自的当前凭证脱敏串。"""
    from ..backends import backend_registry
    from ...core.routing.registry import model_registry

    found: dict[str, str] = {}

    def put(name: str, prefix: str) -> None:
        key = name.strip()
        text = prefix.strip()[:AUDIT_KEY_STORE_MAX]
        if not key or not text:
            return
        found[key] = text
        stem = _gateway_slot_stem(key)
        if not stem or stem == key:
            return
        previous = found[stem] if stem in found else ""
        picked = _prefer_slot_value({previous, text} if previous else {text})
        if picked:
            found[stem] = picked

    for adapter in backend_registry.all():
        put(adapter.name, adapter.audit_key_prefix())
    rh_key = found["rh_app"] if "rh_app" in found else (found["comfyui"] if "comfyui" in found else "")
    if rh_key:
        if "runninghub" not in found:
            put("runninghub", rh_key)
        if "comfyui" not in found:
            put("comfyui", rh_key)
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
_REMAINING_PREFIX = "remaining="


def _marker_path() -> Path:
    from ..resource.RESOURCE_PATH import MAIN_PATH

    return MAIN_PATH / "key_mask_head_tail_v2.done"


def parse_remaining_marker(text: str) -> int | None:
    """磁盘标记里的残留条数。旧 ``done`` 或坏文本视为未知,须再探测 COUNT。"""
    stripped = text.strip()
    if not stripped.startswith(_REMAINING_PREFIX):
        return None
    rest = stripped[len(_REMAINING_PREFIX) :]
    if not rest.isdigit():
        return None
    return int(rest)


def _write_remaining(marker: Path, remaining: int) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{_REMAINING_PREFIX}{remaining}", encoding="utf-8")


def _read_remaining(marker: Path) -> int | None:
    if not marker.is_file():
        return None
    return parse_remaining_marker(marker.read_text(encoding="utf-8"))


async def backfill_historical_key_prefixes() -> int:
    """把旧前 6 位写成当前钥的前5*****后5。空前缀只在首次扫描补。

    先 COUNT 旧前 6 位。磁盘已有 remaining 且 COUNT=0 则只写 remaining=0。
    磁盘 ``remaining=N`` 且 COUNT≤N 视为上次扫完后的不可补残留,跳过翻页。
    无标记(含旧文件 ``done``)时再扫一页空值,尝试用现网钥补历史空行。
    现网一把钥都没有则不写标记,下次启动再探。
    """
    from .models import RHComfyuiTaskRecord

    marker = _marker_path()
    remaining_known = _read_remaining(marker)
    need = await RHComfyuiTaskRecord.count_blank_key_prefix()
    include_empty = remaining_known is None
    if remaining_known is not None and need == 0:
        if remaining_known != 0:
            _write_remaining(marker, 0)
        logger.info("[RHComfyUI] 探测无需凭证脱敏 remaining=0")
        return 0
    if remaining_known is not None and need <= remaining_known:
        if need < remaining_known:
            _write_remaining(marker, need)
        logger.info(f"[RHComfyUI] 探测残留={need} 不超过上次 remaining={remaining_known},跳过扫描")
        return 0
    prefixes = collect_audit_prefixes()
    if not prefixes:
        logger.info(f"[RHComfyUI] 探测待补={need} 但现网无凭证,跳过且不写标记")
        return 0
    filled = 0
    after_id = 0
    while True:
        page = await RHComfyuiTaskRecord.list_blank_key_prefix_page(
            after_id=after_id,
            limit=_PAGE_SIZE,
            include_empty=include_empty,
        )
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
    leftover = await RHComfyuiTaskRecord.count_blank_key_prefix()
    _write_remaining(marker, leftover)
    if filled:
        logger.info(f"[RHComfyUI] 已为 {filled} 条历史任务补上凭证脱敏 remaining={leftover}")
    else:
        logger.info(f"[RHComfyUI] 凭证脱敏本轮未写到行 remaining={leftover}")
    return filled


def _log_backfill_task(done: asyncio.Task[int]) -> None:
    if done.cancelled():
        return
    exc = done.exception()
    if exc is not None:
        logger.warning(f"[RHComfyUI] 历史凭证脱敏补写失败: {exc}")


def schedule_key_prefix_backfill() -> None:
    """丢到后台。启动钩子里同步扫表会和宿主的写入抢同一把写锁。"""
    task = asyncio.create_task(backfill_historical_key_prefixes(), name="rh-key-prefix-backfill")
    task.add_done_callback(_log_backfill_task)


__all__ = [
    "prefix_for_row",
    "collect_audit_prefixes",
    "parse_remaining_marker",
    "backfill_historical_key_prefixes",
    "schedule_key_prefix_backfill",
]
