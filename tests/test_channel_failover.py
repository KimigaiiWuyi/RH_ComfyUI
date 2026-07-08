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
