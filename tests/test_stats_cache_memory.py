"""消费统计：内存标脏仍能立刻返回，不扫表。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from RH_ComfyUI.utils.database import stats_cache


def test_invalidate_keeps_memory_payload() -> None:
    stats_cache._L1.clear()
    key = stats_cache.make_cache_key("summary", bot_id="canvas")
    stats_cache._l1_put(key, {"total": 9, "success": 1}, dirty=False)

    async def _go() -> None:
        await stats_cache.invalidate_stats_cache("canvas")
        hit = await stats_cache.cache_get(key)
        assert hit == {"total": 9, "success": 1}
        entry = stats_cache._l1_get(key)
        assert entry is not None
        assert entry.dirty is True

    asyncio.run(_go())
    stats_cache._L1.clear()


def test_cache_key_stable_for_sliding_days() -> None:
    """days=N 窗口不把 now() 写进键。"""
    t0 = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(seconds=1)
    a = stats_cache.make_cache_key(
        "summary", bot_id="canvas", start_time=t0, end_time=t1, days=14
    )
    b = stats_cache.make_cache_key(
        "summary",
        bot_id="canvas",
        start_time=t0 + timedelta(minutes=5),
        end_time=t1 + timedelta(minutes=5),
        days=14,
    )
    assert a == b
    c = stats_cache.make_cache_key("summary", bot_id="canvas", days=30)
    assert c != a


def test_cache_key_all_history_ignores_clock() -> None:
    a = stats_cache.make_cache_key("summary", bot_id="canvas")
    b = stats_cache.make_cache_key(
        "summary", bot_id="canvas", start_time=None, end_time=None, days=None
    )
    assert a == b
    custom = stats_cache.make_cache_key(
        "summary",
        bot_id="canvas",
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert custom != a


def test_needs_refresh_coalesces_dirty() -> None:
    stats_cache._L1.clear()
    key = stats_cache.make_cache_key("summary", bot_id="canvas")
    stats_cache._l1_put(key, {"total": 1}, dirty=True)
    entry = stats_cache._l1_get(key)
    assert entry is not None
    assert stats_cache._needs_refresh(entry) is False
    entry.computed_mono -= stats_cache._L1_MIN_RECOMPUTE_SEC + 1
    assert stats_cache._needs_refresh(entry) is True
    entry.dirty = False
    assert stats_cache._needs_refresh(entry) is False
    stats_cache._L1.clear()
