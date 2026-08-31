"""消费统计缓存 — admin /stats 的 summary + TOP 用户。

读路径以进程内存为准（可脏读立刻返回），聚合结果后台刷新后再异步 upsert 表。
流水 ``RHComfyuiTaskRecord`` 仍是计费事实源，禁止改成只活在内存里。

写任务记录时只把内存标脏，**不**同步 DELETE 缓存表（避免生成热路径打 SQLite）。
"""

from __future__ import annotations

import json
import time
import asyncio
import hashlib
from typing import Any, Callable, Optional, Coroutine
from datetime import datetime
from dataclasses import dataclass

from gsuid_core.logger import logger

# 同一 bot 全表 SUM 的最小间隔。脏了仍立刻返回上一份，到期才后台重算。
# 以前 dirty 无视年龄 → 每条生成后下一次 /admin/stats 都扫流水表。
_L1_MIN_RECOMPUTE_SEC = 30.0
_L1_MAX = 128

# L2 表 TTL（重启后的温缓存；活进程不靠它挡刷新）
_TTL_WINDOWED = 120
_TTL_ALL_HISTORY = 6 * 3600


@dataclass
class _MemEntry:
    payload: Any
    computed_mono: float
    dirty: bool = False


_L1: dict[str, _MemEntry] = {}
_refreshing: dict[str, asyncio.Task[None]] = {}


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
    """稳定短键;内容哈希防超长。

    滑动窗（``days=N`` / 全部时间）**不要**把 ``now()`` 写进键，否则每次
    /admin/stats 都是新 key → L1/L2 永远 miss → 共享库全表 SUM。
    自定义 ``date_from``/``date_to`` 才用起止时刻。
    """
    if days is not None:
        window = f"d{int(days)}"
    elif start_time is None and end_time is None:
        window = "all"
    else:
        window = f"{_iso(start_time)}|{_iso(end_time)}"
    raw = "|".join(["rh-receipt-aware-20260831-v1", kind, bot_id or "", window, str(int(top_n or 0))])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{bot_id or '_'}:{digest}"[:256]


def _l1_matches_bot(key: str, bot_id: Optional[str]) -> bool:
    if bot_id is None:
        return True
    return key.startswith(f"summary:{bot_id}:") or key.startswith(f"user_summaries:{bot_id}:")


def _l1_get(key: str) -> _MemEntry | None:
    return _L1.get(key)


def _l1_put(key: str, payload: Any, *, dirty: bool = False) -> None:
    if len(_L1) >= _L1_MAX:
        for i, k in enumerate(list(_L1.keys())):
            if i >= _L1_MAX // 2:
                break
            _L1.pop(k, None)
    _L1[key] = _MemEntry(payload=payload, computed_mono=time.monotonic(), dirty=dirty)


def _l1_mark_dirty(bot_id: Optional[str] = None) -> None:
    for k, entry in _L1.items():
        if _l1_matches_bot(k, bot_id):
            entry.dirty = True


def _spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any] | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(coro)

    def _done(t: asyncio.Task[Any]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.debug(f"[stats_cache] bg: {type(exc).__name__}: {exc}")

    task.add_done_callback(_done)
    return task


def _needs_refresh(entry: _MemEntry | None) -> bool:
    if entry is None:
        return True
    age = time.monotonic() - entry.computed_mono
    if age < _L1_MIN_RECOMPUTE_SEC:
        return False
    return entry.dirty


async def cache_get(key: str) -> Any | None:
    """内存优先；没有再读表（重启温启动）。过期/脏的内存仍返回，由上层决定是否刷新。"""
    entry = _l1_get(key)
    if entry is not None:
        return entry.payload
    try:
        from .models import RHComfyuiStatsCache

        raw = await RHComfyuiStatsCache.get_valid(key)
        if not raw:
            return None
        payload = json.loads(raw)
        _l1_put(key, payload, dirty=False)
        return payload
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[stats_cache] get miss/err key={key}: {e}")
        return None


async def _l2_upsert(
    key: str,
    *,
    bot_id: str,
    kind: str,
    payload: Any,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    days: Optional[int],
) -> None:
    from .models import RHComfyuiStatsCache

    ttl = _ttl_for(start_time, end_time, days)
    await RHComfyuiStatsCache.upsert(
        cache_key=key,
        bot_id=bot_id or "",
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
        ttl_seconds=ttl,
    )


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
    """立刻写内存；表 upsert 丢到后台，不挡 HTTP。"""
    _l1_put(key, payload, dirty=False)
    _spawn(
        _l2_upsert(
            key,
            bot_id=bot_id,
            kind=kind,
            payload=payload,
            start_time=start_time,
            end_time=end_time,
            days=days,
        )
    )


async def invalidate_stats_cache(bot_id: Optional[str] = None) -> None:
    """生成 begin/done/退款：只标脏内存，不打库。

    原先每次 DELETE ``rhcomfyuistatscache``，高峰一天几千次写锁。
    后台刷新成功后会 upsert 覆盖行；TTL 到期自然失效。
    """
    _l1_mark_dirty(bot_id)


def _spawn_refresh(key: str, factory: Callable[[], Coroutine[Any, Any, Any]]) -> None:
    existing = _refreshing.get(key)
    if existing is not None and not existing.done():
        return

    async def _run() -> None:
        try:
            payload = await factory()
            _l1_put(key, payload, dirty=False)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[stats_cache] refresh fail key={key}: {e}")
        finally:
            cur = _refreshing.get(key)
            if cur is not None and cur.done():
                _refreshing.pop(key, None)

    task = _spawn(_run())
    if task is not None:
        _refreshing[key] = task


async def get_summary_cached(
    *,
    bot_id: Optional[str],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    days: Optional[int] = None,
) -> dict[str, Any]:
    """内存命中立刻返回。脏且距上次全表 SUM ≥ 30s 才后台重算。冷启动才同步扫表。"""
    from .models import RHComfyuiTaskRecord

    bid = bot_id or ""
    key = make_cache_key(
        "summary",
        bot_id=bid,
        start_time=start_time,
        end_time=end_time,
        days=days,
    )

    async def _compute() -> dict[str, Any]:
        summary = await RHComfyuiTaskRecord.get_summary(
            start_time=start_time,
            end_time=end_time,
            bot_id=bot_id,
        )
        payload: dict[str, Any] = dict(summary)
        await cache_set(
            key,
            bot_id=bid,
            kind="summary",
            payload=payload,
            start_time=start_time,
            end_time=end_time,
            days=days,
        )
        return payload

    entry = _l1_get(key)
    if entry is not None:
        if _needs_refresh(entry):
            _spawn_refresh(key, _compute)
        return entry.payload  # type: ignore[return-value]

    hit = await cache_get(key)
    if isinstance(hit, dict) and "total" in hit:
        if _needs_refresh(_l1_get(key)):
            _spawn_refresh(key, _compute)
        return hit

    return await _compute()


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

    async def _compute() -> list[dict[str, Any]]:
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

    entry = _l1_get(key)
    if entry is not None:
        if _needs_refresh(entry):
            _spawn_refresh(key, _compute)
        return entry.payload  # type: ignore[return-value]

    hit = await cache_get(key)
    if isinstance(hit, list):
        if _needs_refresh(_l1_get(key)):
            _spawn_refresh(key, _compute)
        return hit

    return await _compute()


__all__ = [
    "make_cache_key",
    "cache_get",
    "cache_set",
    "invalidate_stats_cache",
    "get_summary_cached",
    "get_user_summaries_cached",
]
