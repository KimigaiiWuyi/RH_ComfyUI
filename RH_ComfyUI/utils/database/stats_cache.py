"""消费统计表缓存 — admin /stats 的 summary + TOP 用户。

两层:
  L1 进程内存(默认 8s):同进程连点刷新不打 DB
  L2 ``RHComfyuiStatsCache`` 表(默认 45~90s):跨请求复用聚合结果

写任务记录(begin/record/refund)时调用 ``invalidate_stats_cache``。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any, Optional

from gsuid_core.logger import logger

# L1: key -> (expire_monotonic, payload)
_L1: dict[str, tuple[float, Any]] = {}
_L1_TTL_SEC = 8.0
_L1_MAX = 128

# L2 表 TTL
_TTL_WINDOWED = 45  # 有 days / date 范围
_TTL_ALL_HISTORY = 90  # 全历史更贵,缓存稍长


def _ttl_for(start_time: Optional[datetime], end_time: Optional[datetime], days: Optional[int]) -> int:
    if start_time is not None or end_time is not None or days is not None:
        return _TTL_WINDOWED
    return _TTL_ALL_HISTORY


def _iso(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    try:
        return dt.isoformat()
    except Exception:
        return str(dt)


def make_cache_key(
    kind: str,
    *,
    bot_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    days: Optional[int] = None,
    top_n: int = 0,
) -> str:
    """稳定短键;内容哈希防超长。"""
    raw = "|".join(
        [
            kind,
            bot_id or "",
            _iso(start_time),
            _iso(end_time),
            str(days if days is not None else ""),
            str(int(top_n or 0)),
        ]
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{bot_id or '_'}:{digest}"[:256]


def _l1_get(key: str) -> Any | None:
    hit = _L1.get(key)
    if not hit:
        return None
    exp, payload = hit
    if exp <= time.monotonic():
        _L1.pop(key, None)
        return None
    return payload


def _l1_set(key: str, payload: Any) -> None:
    if len(_L1) >= _L1_MAX:
        # 简单淘汰:清一半最旧(无序 dict 近似)
        for i, k in enumerate(list(_L1.keys())):
            if i >= _L1_MAX // 2:
                break
            _L1.pop(k, None)
    _L1[key] = (time.monotonic() + _L1_TTL_SEC, payload)


def _l1_clear(bot_id: Optional[str] = None) -> None:
    if bot_id is None:
        _L1.clear()
        return
    # keys 形如 kind:bot_id:hash
    for k in list(_L1.keys()):
        if k.startswith(f"summary:{bot_id}:") or k.startswith(f"user_summaries:{bot_id}:"):
            _L1.pop(k, None)


async def cache_get(key: str) -> Any | None:
    """L1 → L2 表。"""
    local = _l1_get(key)
    if local is not None:
        return local
    try:
        from .models import RHComfyuiStatsCache

        raw = await RHComfyuiStatsCache.get_valid(key)
        if not raw:
            return None
        payload = json.loads(raw)
        _l1_set(key, payload)
        return payload
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[stats_cache] get miss/err key={key}: {e}")
        return None


async def cache_set(
    key: str,
    *,
    bot_id: str,
    kind: str,
    payload: Any,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    days: Optional[int] = None,
) -> None:
    """写 L1 + L2 表。"""
    _l1_set(key, payload)
    ttl = _ttl_for(start_time, end_time, days)
    try:
        from .models import RHComfyuiStatsCache

        await RHComfyuiStatsCache.upsert(
            cache_key=key,
            bot_id=bot_id or "",
            kind=kind,
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
            ttl_seconds=ttl,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[stats_cache] set err key={key}: {e}")


async def invalidate_stats_cache(bot_id: Optional[str] = None) -> None:
    """任务写入后失效。bot_id 空=全清。"""
    _l1_clear(bot_id)
    try:
        from .models import RHComfyuiStatsCache

        n = await RHComfyuiStatsCache.invalidate(bot_id=bot_id)
        logger.debug(f"[stats_cache] invalidate bot_id={bot_id!r} deleted≈{n}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[stats_cache] invalidate err: {e}")


async def get_summary_cached(
    *,
    bot_id: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    days: Optional[int] = None,
) -> dict[str, Any]:
    """带缓存的 get_summary。"""
    from .models import RHComfyuiTaskRecord

    bid = bot_id or ""
    key = make_cache_key(
        "summary",
        bot_id=bid,
        start_time=start_time,
        end_time=end_time,
        days=days,
    )
    hit = await cache_get(key)
    if isinstance(hit, dict) and "total" in hit:
        return hit  # type: ignore[return-value]

    summary = await RHComfyuiTaskRecord.get_summary(
        start_time=start_time,
        end_time=end_time,
        bot_id=bot_id,
    )
    await cache_set(
        key,
        bot_id=bid,
        kind="summary",
        payload=dict(summary),
        start_time=start_time,
        end_time=end_time,
        days=days,
    )
    return summary


async def get_user_summaries_cached(
    *,
    bot_id: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    top_n: int,
    days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """带缓存的 get_user_summaries。"""
    from .models import RHComfyuiTaskRecord

    bid = bot_id or ""
    key = make_cache_key(
        "user_summaries",
        bot_id=bid,
        start_time=start_time,
        end_time=end_time,
        days=days,
        top_n=top_n,
    )
    hit = await cache_get(key)
    if isinstance(hit, list):
        return hit  # type: ignore[return-value]

    rows = await RHComfyuiTaskRecord.get_user_summaries(
        start_time=start_time,
        end_time=end_time,
        top_n=top_n,
        bot_id=bot_id,
    )
    rows = rows or []
    await cache_set(
        key,
        bot_id=bid,
        kind="user_summaries",
        payload=rows,
        start_time=start_time,
        end_time=end_time,
        days=days,
    )
    return rows


__all__ = [
    "make_cache_key",
    "cache_get",
    "cache_set",
    "invalidate_stats_cache",
    "get_summary_cached",
    "get_user_summaries_cached",
]
