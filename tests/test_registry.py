"""ModelRegistry:注册 / 覆盖 / 模糊匹配 / 模态过滤"""

from typing import Any, Optional

from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.types import PortSpec, PortType, NodeOutput
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.base.generation import AIGCGenerationBase
from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding
from RH_ComfyUI.core.routing.registry import ModelRegistry


class FakeModel(AIGCGenerationBase):
    modality = TaskType.IMAGE
    card = ModelCard(description="fake")

    def __init__(self, name: str = "fake_model") -> None:
        self.name = name
        self.display_name = name

    def input_schema(self) -> dict[str, PortSpec]:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(channel=LocalChannel("local"))]

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        return NodeOutput(output_type="image", data=b"png")


def test_register_get_and_override():
    reg = ModelRegistry()
    reg.register(FakeModel("m1"))
    assert reg.get("m1") is not None
    m1b = FakeModel("m1")
    reg.register(m1b)  # 覆盖(打 warning)
    assert reg.get("m1") is m1b


def test_partial_name_and_modality():
    reg = ModelRegistry()
    reg.register(FakeModel("qwen_2512"))
    reg.register(FakeModel("banana2"))
    assert reg.find_by_partial_name("qwen", TaskType.IMAGE).name == "qwen_2512"
    assert reg.find_by_partial_name("qwen", TaskType.VIDEO) is None
    assert len(reg.by_modality(TaskType.IMAGE)) == 2
