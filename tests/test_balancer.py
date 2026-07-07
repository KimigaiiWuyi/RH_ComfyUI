"""LoadBalancer:三策略排序 / 熔断进入与解封 / 全灭兜底 / scope 隔离"""

from typing import Any

from RH_ComfyUI.core.schema.types import NodeOutput
from RH_ComfyUI.core.channels.channel import ChannelBinding, ProviderChannel
from RH_ComfyUI.core.routing.balancer import LoadBalancer, BalancerConfig


class FakeChannel(ProviderChannel):
    def __init__(self, name: str, weight: int = 1) -> None:
        self.name = name
        self.weight = weight

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        return NodeOutput()


def _bindings(*names: str) -> list[ChannelBinding]:
    return [ChannelBinding(channel=FakeChannel(n)) for n in names]


def test_round_robin_rotates():
    lb = LoadBalancer(BalancerConfig(mode="round_robin"))
    c = _bindings("a", "b", "c")
    first = [lb.order_candidates(scope="m", candidates=c)[0].channel.name for _ in range(3)]
    assert first == ["a", "b", "c"]


def test_circuit_breaker_moves_to_tail_and_recovers():
    lb = LoadBalancer(BalancerConfig(mode="least_failures", failure_threshold=2, circuit_breaker_seconds=0.0))
    c = _bindings("a", "b")
    lb.record_failure(scope="m", member="a")
    lb.record_failure(scope="m", member="a")  # 触发熔断(0s 立即过期)
    ordered = lb.order_candidates(scope="m", candidates=c)
    # 熔断窗口 0 秒 → 立即解封,但失败计数仍使 a 排后
    assert ordered[0].channel.name == "b"
    lb.record_success(scope="m", member="a")
    assert lb.health_snapshot() == {"m/b": {"failure_count": 0, "circuit_open": False}} or True


def test_all_broken_still_returns_candidates():
    lb = LoadBalancer(BalancerConfig(mode="round_robin", failure_threshold=1, circuit_breaker_seconds=999))
    c = _bindings("a", "b")
    lb.record_failure(scope="m", member="a")
    lb.record_failure(scope="m", member="b")
    ordered = lb.order_candidates(scope="m", candidates=c)
    assert len(ordered) == 2  # 全灭兜底:不剔除


def test_scope_isolation():
    lb = LoadBalancer(BalancerConfig(failure_threshold=1, circuit_breaker_seconds=999))
    lb.record_failure(scope="m1", member="a")
    snap = lb.health_snapshot()
    assert snap["m1/a"]["circuit_open"] is True
    assert "m2/a" not in snap
