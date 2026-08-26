"""PollingChannelMixin — "创建任务 → 轮询终态"型上游的通用骨架

自 backends/seedance/provider.py 的 poll_until_done 提炼泛化。
Seedance 自己继续用自己的实现,不强迁;本 Mixin 供新的异步轮询型通道复用。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Awaitable

from gsuid_core.logger import logger

from ..base.errors import ChannelError


class PollingChannelMixin:
    """子类提供 poll_once(task_id) -> (done: bool, payload);本类提供节奏与超时"""

    poll_interval_seconds: float = 5.0
    poll_timeout_seconds: float = 3600.0
    heartbeat_every: int = 6  # 每 N 次轮询打一条心跳日志

    async def poll_until_done(
        self,
        task_id: str,
        poll_once: Callable[[str], Awaitable[tuple[bool, Any]]],
        *,
        on_tick: Optional[Callable[[int], Awaitable[None]]] = None,
    ) -> Any:
        """轮询直到 poll_once 返回 done=True;超时抛 ChannelError(retryable=False)"""
        elapsed = 0.0
        tick = 0
        while elapsed < self.poll_timeout_seconds:
            done, payload = await poll_once(task_id)
            if done:
                return payload
            tick += 1
            if tick % self.heartbeat_every == 0:
                logger.info(f"[PollingChannel] task={task_id} 已等待 {elapsed:.0f}s")
            if on_tick is not None:
                await on_tick(tick)
            await asyncio.sleep(self.poll_interval_seconds)
            elapsed += self.poll_interval_seconds
        raise ChannelError(
            f"任务 {task_id} 轮询超时({self.poll_timeout_seconds:.0f}s)",
            retryable=False,
        )


__all__ = ["PollingChannelMixin"]
