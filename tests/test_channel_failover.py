"""多供应商故障切换 — Adapter 通道失败须翻译成可重试 ChannelError

回归点:banana2 等桥接模型接了第二路供应商后,第一路(OpenAI 兼容网关)
上游失败必须 fallover 到下一路,而不是把裸 RuntimeError 抛穿导致整单失败。
"""

import asyncio
from typing import Any, Optional

import pytest

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.utils.backends import backend_registry
from RH_ComfyUI.core.base.errors import ChannelError
from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.types import PortSpec, PortType, NodeOutput, CapabilityManifest
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.base import Adapter
from RH_ComfyUI.core.base.generation import AIGCGenerationBase
from RH_ComfyUI.core.channels.channel import ChannelBinding, ProviderChannel
from RH_ComfyUI.core.routing.balancer import LoadBalancer, BalancerConfig


class _BoomAdapter(Adapter):
    name = "boom-backend"

    async def check_available(self) -> bool:
        return True

    async def get_unavailable_reason(self) -> str:
        return ""

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest()

    async def execute(self, request: Any, node: Any, *, on_progress: Any = None) -> NodeOutput:
        raise RuntimeError("上游 500")


def test_adapter_channel_failure_is_retryable_channel_error():
    backend_registry.register(_BoomAdapter())
    ch = AdapterChannel("boom-backend")
    with pytest.raises(ChannelError) as ei:
        asyncio.run(ch.invoke(request=None, node=None, on_progress=None))
    assert ei.value.retryable is True
    assert "上游 500" in ei.value.user_message


class _GoodChannel(ProviderChannel):
    name = "good"

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        return NodeOutput(output_type="image", data=b"ok")


class _TwoChannelModel(AIGCGenerationBase):
    modality = TaskType.IMAGE
    card = ModelCard(description="x")

    def __init__(self) -> None:
        self.name = "failover_model"
        self.display_name = "failover_model"

    def input_schema(self) -> dict:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        backend_registry.register(_BoomAdapter())
        return [ChannelBinding(AdapterChannel("boom-backend")), ChannelBinding(_GoodChannel())]

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        return await binding.channel.invoke(request=request, node=None, on_progress=on_progress)

    def balancer(self) -> LoadBalancer:
        # 固定顺序,先试会失败的 boom,证明确实 fallover 到了 good
        return LoadBalancer(BalancerConfig(mode="least_failures"))


def test_run_falls_over_to_next_provider():
    model = _TwoChannelModel()
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")
    out = asyncio.run(model.run(req))
    assert out.data == b"ok"


class _RejectChannel(ProviderChannel):
    name = "reject"

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        raise ChannelError("参数被拒", retryable=False)


class _NonRetryableModel(AIGCGenerationBase):
    modality = TaskType.IMAGE
    card = ModelCard(description="x")

    def __init__(self, balancer: LoadBalancer) -> None:
        self.name = "nonretry_model"
        self.display_name = "nonretry_model"
        self._balancer = balancer

    def input_schema(self) -> dict:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(_RejectChannel()), ChannelBinding(_GoodChannel())]

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        return await binding.channel.invoke(request=request)

    def balancer(self) -> LoadBalancer:
        return self._balancer


def test_non_retryable_error_skips_breaker_and_failover():
    # retryable=False:不切换通道、不计入熔断(通道是健康的,坏的是参数)
    lb = LoadBalancer(BalancerConfig(mode="least_failures", failure_threshold=1))
    model = _NonRetryableModel(lb)
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")
    with pytest.raises(ChannelError) as ei:
        asyncio.run(model.run(req))
    assert ei.value.retryable is False
    assert lb.health_snapshot() == {}  # 未记失败、未熔断


class _RateLimitedChannel(ProviderChannel):
    """首次 429(transient),重试即成功 — 模拟上游瞬时限流"""

    name = "ratelimited"

    def __init__(self) -> None:
        self.calls = 0

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        self.calls += 1
        if self.calls == 1:
            raise ChannelError("HTTP 429", retryable=True, transient=True, channel=self.name)
        return NodeOutput(output_type="image", data=b"retried-ok")


class _TransientModel(AIGCGenerationBase):
    modality = TaskType.IMAGE
    card = ModelCard(description="x")
    transient_retry_delay = 0.0  # 单测不真等退避
    transient_retry_max_delay = 0.0
    transient_retry_max_wait = 3600.0

    def __init__(self, channel: ProviderChannel, balancer: LoadBalancer) -> None:
        self.name = "transient_model"
        self.display_name = "transient_model"
        self._channel = channel
        self._balancer = balancer

    def input_schema(self) -> dict:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(self._channel)]

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        return await binding.channel.invoke(request=request)

    def balancer(self) -> LoadBalancer:
        return self._balancer


def test_transient_error_retries_same_channel_until_success():
    # 429/503:原通道排队退避,成功前不计熔断
    lb = LoadBalancer(BalancerConfig(mode="least_failures", failure_threshold=1))
    ch = _RateLimitedChannel()
    model = _TransientModel(ch, lb)
    out = asyncio.run(model.run(GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")))
    assert out.data == b"retried-ok"
    assert ch.calls == 2  # 第一次 429,原通道再试成功
    assert lb.health_snapshot() == {}  # 瞬时失败未计入熔断


class _AlwaysRateLimitedChannel(_RateLimitedChannel):
    name = "always429"

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        self.calls += 1
        raise ChannelError("HTTP 429", retryable=True, transient=True, channel=self.name)


def test_transient_queue_budget_exhausted_then_fails():
    # 排队预算耗尽(max_wait=0 → 首次 transient 即超时):记熔断并失败
    lb = LoadBalancer(BalancerConfig(mode="least_failures", failure_threshold=5))
    ch = _AlwaysRateLimitedChannel()
    model = _TransientModel(ch, lb)
    model.transient_retry_max_wait = 0.0
    with pytest.raises((ChannelError, Exception)) as ei:
        asyncio.run(model.run(GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")))
    # 预算 0:首次 429 即放弃,只调用 1 次
    assert ch.calls == 1
    snapshot = lb.health_snapshot()
    assert snapshot.get("transient_model/always429", {}).get("failure_count") == 1
    # 用户文案应体现排队超时
    err = ei.value
    from RH_ComfyUI.core.base.errors import AllChannelsFailedError

    root = err.cause if isinstance(err, AllChannelsFailedError) else err
    assert root is not None
    assert "排队" in str(getattr(root, "user_message", "") or root) or getattr(root, "code", "") == (
        "TRANSIENT_QUEUE_TIMEOUT"
    )


def test_transient_queue_retries_multiple_times_within_budget():
    # 预算内可多次重试(delay=0 加速)
    lb = LoadBalancer(BalancerConfig(mode="least_failures", failure_threshold=99))
    ch = _AlwaysRateLimitedChannel()
    model = _TransientModel(ch, lb)
    model.transient_retry_max_wait = 0.05  # 50ms 窗口,delay=0 可连打多次
    with pytest.raises(Exception):
        asyncio.run(model.run(GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")))
    assert ch.calls >= 2  # 至少重试过
