"""消费统计：内存标脏仍能立刻返回，不扫表。"""

from __future__ import annotations

import asyncio

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
