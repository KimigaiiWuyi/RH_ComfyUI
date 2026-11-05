"""PollingChannelMixin + PollErrorTolerance — 轮询阶段容错

``PollErrorTolerance``:轮询 GET 的传输错误与 408/429/5xx 在窗口内
退避重试(不往上抛),窗口耗尽或不可识别错误才抛原错误码。
任务号已在上游手里,盲目换源会重复建单 —— 先给上游恢复时间。

``PollingChannelMixin``:"创建任务 → 轮询终态"型上游的通用骨架。
"""

from __future__ import annotations

import time
import asyncio
from typing import Any, Callable, Optional, Awaitable

from gsuid_core.logger import logger

from .failure import Phase, FailureKind
from ..base.errors import ChannelError

# 可被容忍的轮询错误:http_status=None(传输) / 408 / 429 / 5xx
_TOLERABLE_4XX = (408, 429)


class PollErrorTolerance:
    """轮询错误容忍窗:连续出错未超 ``window_s`` → 返回退避秒数;否则 None。"""

    def __init__(
        self,
        *,
        window_s: float = 600.0,
        base_delay: float = 2.0,
        max_delay: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_s
        self._base = base_delay
        self._max = max_delay
        self._clock = clock
        self._first_at: Optional[float] = None
        self._consecutive = 0

    def next_delay(self, *, http_status: Optional[int]) -> Optional[float]:
        """http_status=None 表示传输错误;不可容忍或超窗 → None(抛原错)。"""
        if http_status is not None and not (http_status in _TOLERABLE_4XX or http_status >= 500):
            return None
        now = self._clock()
        if self._first_at is None:
            self._first_at = now
        elif now - self._first_at > self._window:
            return None
        self._consecutive += 1
        return min(self._base * (2 ** (self._consecutive - 1)), self._max)

    def reset(self) -> None:
        self._first_at = None
        self._consecutive = 0


class PollingChannelMixin:
    """子类提供 poll_once(task_id) -> (done: bool, payload);本类提供节奏与超时"""

    poll_interval_seconds: float = 5.0
    poll_timeout_seconds: float = 3600.0
    heartbeat_every: int = 6  # 每 N 次轮询打一条心跳日志
    poll_error_window_s: float = 600.0

    async def poll_until_done(
        self,
        task_id: str,
        poll_once: Callable[[str], Awaitable[tuple[bool, Any]]],
        *,
        on_tick: Optional[Callable[[int], Awaitable[None]]] = None,
    ) -> Any:
        """轮询直到 poll_once 返回 done=True;超时抛 ChannelError(phase=ACCEPTED)"""
        elapsed = 0.0
        tick = 0
        tolerance = PollErrorTolerance(window_s=self.poll_error_window_s)
        while elapsed < self.poll_timeout_seconds:
            try:
                done, payload = await poll_once(task_id)
            except ChannelError as exc:
                # 可识别 http_status 的后端错误在窗口内容忍;4xx(非408/429)立即抛
                delay = tolerance.next_delay(http_status=exc.http_status)
                if delay is None or exc.resolved_kind is FailureKind.INVALID_REQUEST:
                    raise
                await asyncio.sleep(delay)
                elapsed += delay
                continue
            except (Exception,) as exc:  # noqa: BLE001 - 传输类错误窗口内容忍
                if not _is_transport(exc):
                    raise
                delay = tolerance.next_delay(http_status=None)
                if delay is None:
                    raise ChannelError(
                        f"任务 {task_id} 轮询网络错误({type(exc).__name__}: {exc})",
                        kind=FailureKind.PROVIDER_FAULT,
                        phase=Phase.ACCEPTED,
                        code="POLL_NETWORK_ERROR",
                        user_message="轮询阶段网络异常,请稍后重试。",
                    ) from exc
                await asyncio.sleep(delay)
                elapsed += delay
                continue
            tolerance.reset()
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
            kind=FailureKind.PROVIDER_FAULT,
            phase=Phase.ACCEPTED,
            code="POLL_TIMEOUT",
            retryable=False,
        )


def _is_transport(exc: BaseException) -> bool:
    """宽松判定传输类错误(httpx/aiohttp/socket/timeout);业务错误不算。"""
    import httpx

    if isinstance(exc, (httpx.HTTPError, asyncio.TimeoutError, OSError)):
        return True
    try:
        import aiohttp

        if isinstance(exc, aiohttp.ClientError):
            return True
    except ImportError:
        pass
    return False


__all__ = ["PollingChannelMixin", "PollErrorTolerance"]
