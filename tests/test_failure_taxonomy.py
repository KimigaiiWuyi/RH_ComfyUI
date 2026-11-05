"""失败分类与熔断收口的行为测试(离线)

契约:
- classify_http_status 表驱动,与旧 http_status_retryable 口径等价(402 除外,见注释);
- ChannelError 旧式构造的 retryable/transient 与旧实现完全一致;
- decide_on_failure 是换源/熔断的唯一决策点(不测实现,测三态输出);
- ChannelHealth 两级熔断:OPEN/HALF_OPEN/指数退避/探测令牌/AUTH 长冷却;
- health_snapshot 旧键保留;
- PollErrorTolerance 吸收轮询 5xx,超窗抛原错误码且 retryable=False,4xx 立即抛。
"""

from __future__ import annotations

from typing import Any

import pytest

from RH_ComfyUI.core.base.errors import ChannelError
from RH_ComfyUI.core.schema.types import NodeOutput
from RH_ComfyUI.core.base.generation import decide_on_failure, effective_retryable
from RH_ComfyUI.core.channels.health import ChannelHealth
from RH_ComfyUI.core.channels.channel import ProviderChannel
from RH_ComfyUI.core.channels.failure import (
    Phase,
    Stage,
    FailureKind,
    classify_transport,
    classify_http_status,
    http_status_retryable,
)
from RH_ComfyUI.core.channels.polling import PollErrorTolerance
from RH_ComfyUI.core.routing.balancer import LoadBalancer, BalancerConfig


class _Ch(ProviderChannel):
    """最小通道替身:只提供名字与健康组,不参与执行。"""

    def __init__(self, name: str, group: str | None = None) -> None:
        self.name = name
        self._group = group if group is not None else name

    def health_group(self) -> str:
        return self._group

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        raise NotImplementedError


# ── 表驱动 HTTP 分类 ──


@pytest.mark.parametrize(
    "status,expected_kind",
    [
        (400, FailureKind.INVALID_REQUEST),
        (404, FailureKind.INVALID_REQUEST),
        (422, FailureKind.INVALID_REQUEST),
        (401, FailureKind.AUTH_OR_QUOTA),
        (402, FailureKind.AUTH_OR_QUOTA),  # 新增:402 起归入 AUTH
        (403, FailureKind.AUTH_OR_QUOTA),
        (408, FailureKind.PROVIDER_FAULT),
        (429, FailureKind.RATE_LIMITED),
        (500, FailureKind.PROVIDER_FAULT),
        (502, FailureKind.PROVIDER_FAULT),
        (503, FailureKind.PROVIDER_FAULT),
        (504, FailureKind.PROVIDER_FAULT),
    ],
)
def test_classify_http_status_kind(status: int, expected_kind: FailureKind) -> None:
    kind, _ = classify_http_status(status, stage="create")
    assert kind is expected_kind


@pytest.mark.parametrize(
    "status,stage,expected_phase",
    [
        (400, "create", Phase.REJECTED),
        (429, "create", Phase.REJECTED),
        (503, "create", Phase.REJECTED),
        (500, "create", Phase.AMBIGUOUS),
        (502, "create", Phase.AMBIGUOUS),
        (504, "create", Phase.AMBIGUOUS),
        (503, "poll", Phase.ACCEPTED),
        (500, "result", Phase.ACCEPTED),
        (400, "other", Phase.UNKNOWN),
    ],
)
def test_classify_http_status_phase(status: int, stage: Stage, expected_phase: Phase) -> None:
    _, phase = classify_http_status(status, stage=stage)
    assert phase is expected_phase


# 旧 http_status_retryable 口径等价(除 402 新增 AUTH 外)
def _old_retryable(status: int) -> bool:
    if status >= 500:
        return True
    return status in (401, 403, 408, 429)


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 408, 422, 429, 500, 502, 503, 504])
def test_shim_matches_old_policy_except_402(status: int) -> None:
    expected = _old_retryable(status) or status == 402
    assert http_status_retryable(status) == expected


def test_classify_transport() -> None:
    import httpx

    try:
        raise httpx.ConnectError("refused")
    except httpx.ConnectError as exc:
        assert classify_transport(exc, stage="create") == (
            FailureKind.PROVIDER_FAULT,
            Phase.BEFORE_SEND,
        )
    try:
        raise httpx.ReadTimeout("slow")
    except httpx.ReadTimeout as exc:
        assert classify_transport(exc, stage="create") == (
            FailureKind.PROVIDER_FAULT,
            Phase.AMBIGUOUS,
        )
        assert classify_transport(exc, stage="poll") == (
            FailureKind.PROVIDER_FAULT,
            Phase.ACCEPTED,
        )
    try:
        raise ValueError("not transport")
    except ValueError as exc:
        assert classify_transport(exc, stage="create") == (
            FailureKind.PROVIDER_FAULT,
            Phase.UNKNOWN,
        )


# ── ChannelError 旧构造向后兼容 ──


@pytest.mark.parametrize(
    "retryable,transient,expected_kind",
    [
        (False, False, FailureKind.INVALID_REQUEST),
        (True, False, FailureKind.PROVIDER_FAULT),
        (True, True, FailureKind.RATE_LIMITED),
    ],
)
def test_legacy_error_infer_kind(retryable: bool, transient: bool, expected_kind: FailureKind) -> None:
    err = ChannelError("x", retryable=retryable, transient=transient)
    assert err.kind is None
    assert err.resolved_kind is expected_kind
    assert err.retryable is retryable
    assert err.transient is transient
    assert err.phase is Phase.UNKNOWN


def test_kind_derives_flags() -> None:
    e = ChannelError("x", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, http_status=503)
    assert e.retryable is True and e.transient is True
    e = ChannelError("x", kind=FailureKind.RATE_LIMITED, phase=Phase.REJECTED, http_status=429)
    assert e.retryable is True and e.transient is True
    e = ChannelError("x", kind=FailureKind.PROVIDER_FAULT, phase=Phase.ACCEPTED)
    assert e.retryable is False  # ACCEPTED 后不再换源(事故修复)
    e = ChannelError("x", kind=FailureKind.TASK_FAILED, phase=Phase.ACCEPTED)
    assert e.retryable is True  # TASK_FAILED 仍可换源(非 strict)
    e = ChannelError("x", kind=FailureKind.INVALID_REQUEST, phase=Phase.REJECTED)
    assert e.retryable is False
    # 显式 retryable=False 覆盖推导(如某家把 402 当不可重试)
    e = ChannelError("x", kind=FailureKind.AUTH_OR_QUOTA, phase=Phase.REJECTED, retryable=False)
    assert e.retryable is False


def test_effective_retryable_accepts_phase_override() -> None:
    # vendor_task_id 已绑定 → phase 覆盖 ACCEPTED → PROVIDER_FAULT 不再换源
    e = ChannelError("x", kind=FailureKind.PROVIDER_FAULT, phase=Phase.AMBIGUOUS, retryable=True)
    assert effective_retryable(e, Phase.AMBIGUOUS) is True
    assert effective_retryable(e, Phase.ACCEPTED) is False


# ── decide_on_failure ──


def test_decide_strict_rejected_failover() -> None:
    # 事故回归:strict + REJECTED 503 → 可换源
    e = ChannelError("x", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, http_status=503)
    assert decide_on_failure(e, phase=Phase.REJECTED, strict=True, has_alternative=True) == "failover"


def test_decide_strict_ambiguous_raise() -> None:
    # 事故回归:strict + AMBIGUOUS(500/ReadTimeout)→ raise 不换源(防重复建单)
    e = ChannelError("x", kind=FailureKind.PROVIDER_FAULT, phase=Phase.AMBIGUOUS, http_status=500)
    assert decide_on_failure(e, phase=Phase.AMBIGUOUS, strict=True, has_alternative=True) == "raise"


def test_decide_strict_legacy_unknown_raise() -> None:
    # 事故回归:旧式 retryable=True(phase UNKNOWN,strict 按 AMBIGUOUS)→ raise
    e = ChannelError("x", retryable=True)
    assert decide_on_failure(e, phase=Phase.UNKNOWN, strict=True, has_alternative=True) == "raise"


def test_decide_accepted_no_failover() -> None:
    # vendor_task_id 后的 PROVIDER_FAULT(phase=ACCEPTED)→ retryable=False → raise,不换源
    e = ChannelError("x", kind=FailureKind.PROVIDER_FAULT, phase=Phase.ACCEPTED)
    assert decide_on_failure(e, phase=Phase.ACCEPTED, strict=False, has_alternative=True) == "raise"


def test_decide_transient_retry_in_place() -> None:
    e = ChannelError("x", kind=FailureKind.RATE_LIMITED, phase=Phase.REJECTED, http_status=429)
    assert decide_on_failure(e, phase=Phase.REJECTED, strict=False, has_alternative=False) == "retry_in_place"
    # 有备选 → 立即换源(不再原地排队 1h)
    assert decide_on_failure(e, phase=Phase.REJECTED, strict=False, has_alternative=True) == "failover"


# ── ChannelHealth 两级熔断 ──


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _mk(**kw) -> ChannelHealth:
    return ChannelHealth(clock=_Clock())


def test_group_and_member_states() -> None:
    lb = LoadBalancer(BalancerConfig(failure_threshold=2, circuit_breaker_seconds=10))
    # 两次 PROVIDER_FAULT → group OPEN
    lb.record_failure(scope="m", member="a", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, group="h1")
    lb.record_failure(scope="m", member="a", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, group="h1")
    snap = lb.health_snapshot()
    assert "group:h1" in snap
    assert snap["group:h1"]["circuit_open"] is True
    assert "failure_count" in snap["group:h1"]
    assert snap["group:h1"]["state"] == "open"


def test_task_failed_member_only() -> None:
    lb = LoadBalancer(BalancerConfig(failure_threshold=1, circuit_breaker_seconds=10))
    lb.record_failure(scope="m", member="a", kind=FailureKind.TASK_FAILED, phase=Phase.ACCEPTED, group="h1")
    snap = lb.health_snapshot()
    assert "m/a" in snap and snap["m/a"]["circuit_open"] is True
    assert "group:h1" not in snap


def test_rate_limited_throttle_not_failure() -> None:
    lb = LoadBalancer(BalancerConfig(failure_threshold=1, circuit_breaker_seconds=10))
    lb.record_failure(scope="m", member="a", kind=FailureKind.RATE_LIMITED, phase=Phase.REJECTED, group="h1")
    snap = lb.health_snapshot()
    assert snap["m/a"]["throttled"] is True
    assert snap["m/a"]["circuit_open"] is False
    assert snap["m/a"]["failure_count"] == 0


def test_rate_limited_member_deprioritized_in_order() -> None:
    """member 节流必须让排序降权:被 429 的候选排进节流桶,垫在未节流候选后。"""
    from RH_ComfyUI.core.channels.channel import ChannelBinding

    lb = LoadBalancer(BalancerConfig(failure_threshold=1, circuit_breaker_seconds=10, mode="round_robin"))
    lb.record_failure(scope="m", member="a", kind=FailureKind.RATE_LIMITED, phase=Phase.REJECTED, group="h1")
    a = ChannelBinding(channel=_Ch("a", "h1"))
    b = ChannelBinding(channel=_Ch("b", "h1"))
    ordered = lb.order_candidates(scope="m", candidates=[a, b])
    assert ordered[-1].channel.name == "a"


def test_half_open_probe_lease_single() -> None:
    clock = _Clock()
    lb = LoadBalancer(
        BalancerConfig(failure_threshold=1, base_cooldown=10.0),
        health=ChannelHealth(clock=clock, jitter=lambda a, b: 0.0),
    )
    lb.record_failure(scope="m", member="a", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, group="g")
    assert lb.health_snapshot()["group:g"]["state"] == "open"
    clock.t = 11.0  # cooldown 到期 → HALF_OPEN
    lease1 = lb.begin_attempt(scope="m", channel=_Ch("a", "g"))
    assert lease1 is not None
    lease2 = lb.begin_attempt(scope="m", channel=_Ch("a", "g"))
    assert lease2 is None  # 同时只放 1 个探测
    lb.record_success(scope="m", member="a", group="g", lease=lease1)
    assert "group:g" not in lb.health_snapshot()


def test_failed_probe_doubles_cooldown() -> None:
    clock = _Clock()
    lb = LoadBalancer(
        BalancerConfig(failure_threshold=1, base_cooldown=10.0, max_cooldown=60.0),
        health=ChannelHealth(clock=clock, jitter=lambda a, b: 0.0),
    )
    lb.record_failure(scope="m", member="a", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, group="g")
    clock.t = 11.0
    lease = lb.begin_attempt(scope="m", channel=_Ch("a", "g"))
    lb.record_failure(
        scope="m", member="a", kind=FailureKind.PROVIDER_FAULT, phase=Phase.REJECTED, group="g", lease=lease
    )
    snap = lb.health_snapshot()["group:g"]
    assert snap["state"] == "open"
    cd = snap["cooldown_remaining"]
    assert isinstance(cd, (int, float)) and not isinstance(cd, bool)
    assert 15 < cd <= 20.5  # 10 * 2^1


def test_auth_long_cooldown() -> None:
    clock = _Clock()
    lb = LoadBalancer(
        BalancerConfig(failure_threshold=1, base_cooldown=10.0),
        health=ChannelHealth(clock=clock, jitter=lambda a, b: 0.0),
    )
    lb.record_failure(scope="m", member="a", kind=FailureKind.AUTH_OR_QUOTA, phase=Phase.REJECTED, group="g")
    snap = lb.health_snapshot()["group:g"]
    cd = snap["cooldown_remaining"]
    assert isinstance(cd, (int, float)) and not isinstance(cd, bool)
    assert cd >= 600.0  # max(10*20, 600)


def test_strict_half_open_ambiguous_not_first() -> None:
    clock = _Clock()
    lb = LoadBalancer(
        BalancerConfig(failure_threshold=1, base_cooldown=10.0, mode="round_robin"),
        health=ChannelHealth(clock=clock, jitter=lambda a, b: 0.0),
    )

    from RH_ComfyUI.core.channels.channel import ChannelBinding

    a = ChannelBinding(channel=_Ch("a"))
    b = ChannelBinding(channel=_Ch("b"))
    # a 在 AMBIGUOUS 失败下开熔断
    lb.record_failure(scope="m", member="a", kind=FailureKind.PROVIDER_FAULT, phase=Phase.AMBIGUOUS, group="a")
    clock.t = 11.0
    ordered = lb.order_candidates(scope="m", candidates=[a, b], strict=True)
    assert ordered[0].channel.name == "b"  # AMBIGUOUS 半开 strict 不排第一
    # 非 strict → 半开可探测排第一
    ordered = lb.order_candidates(scope="m", candidates=[a, b], strict=False)
    assert ordered[0].channel.name == "a"


def test_invalid_request_no_health() -> None:
    lb = LoadBalancer(BalancerConfig(failure_threshold=1, circuit_breaker_seconds=10))
    lb.record_failure(scope="m", member="a", kind=FailureKind.INVALID_REQUEST, phase=Phase.REJECTED, group="g")
    assert "m/a" not in lb.health_snapshot()
    assert "group:g" not in lb.health_snapshot()


def test_snapshot_old_keys() -> None:
    lb = LoadBalancer(BalancerConfig(failure_threshold=1, circuit_breaker_seconds=10))
    lb.record_failure(scope="m", member="a")
    snap = lb.health_snapshot()
    assert "m/a" in snap
    assert "failure_count" in snap["m/a"]
    assert "circuit_open" in snap["m/a"]


# ── PollErrorTolerance ──


def test_poll_tolerance_absorbs_5xx() -> None:
    clock = _Clock()
    tol = PollErrorTolerance(window_s=600.0, base_delay=2.0, max_delay=30.0, clock=clock)
    assert tol.next_delay(http_status=503) == 2.0
    assert tol.next_delay(http_status=500) == 4.0
    assert tol.next_delay(http_status=None) == 8.0
    tol.reset()
    assert tol.next_delay(http_status=503) == 2.0


def test_poll_tolerance_window_exhaust() -> None:
    clock = _Clock()
    tol = PollErrorTolerance(window_s=10.0, base_delay=2.0, max_delay=30.0, clock=clock)
    assert tol.next_delay(http_status=503) is not None
    clock.t = 20.0  # 超窗
    assert tol.next_delay(http_status=503) is None


def test_poll_tolerance_4xx_immediate() -> None:
    tol = PollErrorTolerance()
    assert tol.next_delay(http_status=400) is None
    assert tol.next_delay(http_status=404) is None
    # 408/429 可容忍
    assert tol.next_delay(http_status=408) is not None
    assert tol.next_delay(http_status=429) is not None
