"""通道健康状态机 — 两级熔断(group 级 + member 级),仅进程内存

key 两级:
- ``("group", health_group)``:共享同一上游端点的通道归一组;
  吃 PROVIDER_FAULT / AUTH_OR_QUOTA(端点挂了,组内全部候选一起排后)。
- ``("member", scope, member)``:旧有 (模型,通道) 粒度;
  吃 TASK_FAILED 与旧式(无 kind)record_failure。
  RATE_LIMITED 只写 member 的短节流标记,不计失败;
  INVALID_REQUEST / CAPABILITY 不写任何健康。

状态:CLOSED →(连续失败 ≥ threshold)→ OPEN(cooldown 指数退避+抖动)
→ 到期 HALF_OPEN(同时只放 1 个探测,租约 900s)→ 探测成功 CLOSED / 失败回 OPEN。
重启即全 CLOSED。
"""

from __future__ import annotations

import time
import random
import threading
from enum import Enum
from typing import Union, Callable, Optional
from dataclasses import field, dataclass

from gsuid_core.logger import logger

from .failure import RATE_LIMIT_THROTTLE_S, Phase, FailureKind


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# HALF_OPEN 探测令牌租约:过期自动释放,防持而不还
PROBE_LEASE_S = 900.0


@dataclass
class AttemptLease:
    """一次通道尝试持有的探测令牌(0~2 个 key;CLOSED 时为空)。"""

    keys: tuple[tuple[str, ...], ...] = ()
    released: bool = field(default=False, repr=False)


@dataclass
class _KeyHealth:
    failures: int = 0
    opens: int = 0
    open_until: float = 0.0
    last_kind: Optional[FailureKind] = None
    last_phase: Optional[Phase] = None
    throttled_until: float = 0.0
    probe_lease_until: float = 0.0


@dataclass(frozen=True)
class CandidateHealth:
    """单候选在排序时的健康视图(member 或 group 任一 OPEN 即 OPEN)。"""

    state: CircuitState
    last_phase: Optional[Phase]
    open_until: float
    probe_free: bool
    throttled: bool = False


class ChannelHealth:
    """两级熔断存储与状态机;线程安全;时钟可注入便于测试。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._clock = clock
        self._jitter = jitter
        self._lock = threading.Lock()
        self._keys: dict[tuple[str, ...], _KeyHealth] = {}

    def now(self) -> float:
        return self._clock()

    def _entry(self, key: tuple[str, ...]) -> _KeyHealth:
        h = self._keys.get(key)
        if h is None:
            h = _KeyHealth()
            self._keys[key] = h
        return h

    def _state_of(self, h: _KeyHealth, now: float) -> CircuitState:
        if h.open_until <= 0:
            return CircuitState.CLOSED
        if now < h.open_until:
            return CircuitState.OPEN
        return CircuitState.HALF_OPEN

    def evaluate(self, *, member_key: tuple[str, ...], group_key: tuple[str, ...]) -> CandidateHealth:
        """member/group 合成单候选健康视图(任一 OPEN → OPEN)。"""
        now = self._clock()
        with self._lock:
            out = self._evaluate_locked(member_key=member_key, group_key=group_key, now=now)
        return out

    def _evaluate_locked(
        self,
        *,
        member_key: tuple[str, ...],
        group_key: tuple[str, ...],
        now: float,
    ) -> CandidateHealth:
        m = self._keys.get(member_key)
        g = self._keys.get(group_key)
        states: list[tuple[CircuitState, Optional[Phase], float]] = []
        for h in (m, g):
            if h is not None:
                states.append((self._state_of(h, now), h.last_phase, h.open_until))
        if any(s[0] is CircuitState.OPEN for s in states):
            until = min(s[2] for s in states if s[0] is CircuitState.OPEN)
            phase = next((s[1] for s in states if s[0] is CircuitState.OPEN), None)
            return CandidateHealth(CircuitState.OPEN, phase, until, probe_free=False)
        if any(s[0] is CircuitState.HALF_OPEN for s in states):
            until = max(s[2] for s in states if s[0] is CircuitState.HALF_OPEN)
            phase = next((s[1] for s in states if s[0] is CircuitState.HALF_OPEN), None)
            free = all(
                (h.probe_lease_until <= now)
                for h in (m, g)
                if h is not None and self._state_of(h, now) is CircuitState.HALF_OPEN
            )
            return CandidateHealth(CircuitState.HALF_OPEN, phase, until, probe_free=free)
        # RATE_LIMITED 写在 member;throttle_group 留作组级入口,两级任一命中即节流
        throttled = any(h is not None and now < h.throttled_until for h in (m, g))
        return CandidateHealth(CircuitState.CLOSED, None, 0.0, probe_free=False, throttled=throttled)

    def is_throttled(self, member_key: tuple[str, ...]) -> bool:
        now = self._clock()
        with self._lock:
            h = self._keys.get(member_key)
            return h is not None and now < h.throttled_until

    def begin_attempt(
        self,
        *,
        member_key: tuple[str, ...],
        group_key: tuple[str, ...],
    ) -> AttemptLease | None:
        """原子占用 HALF_OPEN 探测令牌;令牌被占 → None(调用方跳过)。"""
        now = self._clock()
        with self._lock:
            held: list[tuple[str, ...]] = []
            for key in (member_key, group_key):
                h = self._keys.get(key)
                if h is None or self._state_of(h, now) is not CircuitState.HALF_OPEN:
                    continue
                if h.probe_lease_until > now:
                    for k in held:
                        self._keys[k].probe_lease_until = 0.0
                    return None
                h.probe_lease_until = now + PROBE_LEASE_S
                held.append(key)
            return AttemptLease(keys=tuple(held))

    def end_attempt(self, lease: AttemptLease | None) -> None:
        """中性结局(参数类/取消)或兜底:只释放探测令牌。"""
        if lease is None or lease.released:
            return
        lease.released = True
        with self._lock:
            for key in lease.keys:
                h = self._keys.get(key)
                if h is not None:
                    h.probe_lease_until = 0.0

    def record_success(
        self,
        *,
        member_key: tuple[str, ...],
        group_key: tuple[str, ...],
        lease: AttemptLease | None = None,
    ) -> None:
        self.end_attempt(lease)
        with self._lock:
            for key in (member_key, group_key):
                h = self._keys.get(key)
                if h is not None:
                    h.failures = 0
                    h.opens = 0
                    h.open_until = 0.0
                    h.throttled_until = 0.0
                    h.last_kind = None
                    h.last_phase = None

    def record_failure(
        self,
        key: tuple[str, ...],
        *,
        kind: FailureKind,
        phase: Optional[Phase],
        threshold: int,
        base_cooldown: float,
        max_cooldown: float,
    ) -> None:
        """单 key 记一次失败;达阈值转 OPEN 并记 WARNING。"""
        if threshold <= 0:
            return
        now = self._clock()
        with self._lock:
            h = self._entry(key)
            h.failures += 1
            h.last_kind = kind
            h.last_phase = phase
            if h.failures < threshold:
                return
            h.opens += 1
            start = max(base_cooldown * 20, 600.0) if kind is FailureKind.AUTH_OR_QUOTA else base_cooldown
            cooldown = min(start * (2 ** (h.opens - 1)), max_cooldown)
            cooldown *= 1.0 + self._jitter(-0.2, 0.2)
            h.open_until = now + cooldown
            h.probe_lease_until = 0.0
            logger.warning(
                f"[Balancer] {key} 连续失败 {h.failures} 次"
                f"(kind={kind.value}, phase={(phase or Phase.UNKNOWN).value}),熔断 {cooldown:.0f}s"
            )

    def throttle_member(self, member_key: tuple[str, ...], *, seconds: float = RATE_LIMIT_THROTTLE_S) -> None:
        now = self._clock()
        with self._lock:
            self._entry(member_key).throttled_until = now + seconds

    def throttle_group(self, group_key: tuple[str, ...], *, seconds: float = RATE_LIMIT_THROTTLE_S) -> None:
        now = self._clock()
        with self._lock:
            self._entry(group_key).throttled_until = now + seconds

    def snapshot(self) -> dict[str, dict[str, Union[float, int, bool, str]]]:
        """健康快照:member 键 ``{scope}/{member}``,group 键 ``group:{name}``。"""
        now = self._clock()
        out: dict[str, dict[str, Union[float, int, bool, str]]] = {}
        with self._lock:
            items = list(self._keys.items())
        for key, h in items:
            if key[0] == "group":
                name = f"group:{key[1]}"
            else:
                name = f"{key[1]}/{key[2]}"
            state = self._state_of(h, now)
            if h.failures == 0 and state is CircuitState.CLOSED and h.throttled_until <= now and h.last_kind is None:
                continue
            entry: dict[str, Union[float, int, bool, str]] = {
                "failure_count": h.failures,
                "circuit_open": state is CircuitState.OPEN,
                "state": state.value,
                "cooldown_remaining": round(max(0.0, h.open_until - now), 1),
                "last_kind": h.last_kind.value if h.last_kind is not None else "",
                "last_phase": h.last_phase.value if h.last_phase is not None else "",
                "opens": h.opens,
            }
            if h.throttled_until > now:
                entry["throttled"] = True
            out[name] = entry
        return out


__all__ = [
    "AttemptLease",
    "CandidateHealth",
    "ChannelHealth",
    "CircuitState",
    "PROBE_LEASE_S",
]
