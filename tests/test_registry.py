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


class OtherFakeModel(FakeModel):
    pass


def test_register_get_and_override():
    reg = ModelRegistry()
    assert reg.register(FakeModel("m1")) is True
    assert reg.get("m1") is not None
    m1b = FakeModel("m1")
    assert reg.register(m1b) is False  # 同实现静默覆盖实例
    assert reg.get("m1") is m1b


def test_same_impl_reregister_is_silent(caplog):
    import logging

    reg = ModelRegistry()
    reg.register(FakeModel("m1"))
    caplog.set_level(logging.INFO)
    caplog.clear()
    reg.register(FakeModel("m1"))
    text = caplog.text
    assert "重复注册" not in text
    assert "注册模型" not in text


def test_different_impl_reregister_warns(caplog):
    import logging

    reg = ModelRegistry()
    reg.register(FakeModel("m1"))
    caplog.set_level(logging.WARNING)
    caplog.clear()
    reg.register(OtherFakeModel("m1"))
    assert "重复注册" in caplog.text
    assert isinstance(reg.get("m1"), OtherFakeModel)


def test_partial_name_and_modality():
    reg = ModelRegistry()
    reg.register(FakeModel("qwen_2512"))
    reg.register(FakeModel("banana2"))
    m = reg.find_by_partial_name("qwen", TaskType.IMAGE)
    assert m is not None and m.name == "qwen_2512"
    assert reg.find_by_partial_name("qwen", TaskType.VIDEO) is None
    assert len(reg.by_modality(TaskType.IMAGE)) == 2


def test_partial_name_is_case_insensitive_and_prefers_exact():
    """命令入口会把首词 lower();IndexTTS2.5 必须精确命中,不能被 IndexTTS2 抢走。"""
    from RH_ComfyUI.core.routing.registry import match_partial_name

    names = ["IndexTTS2", "IndexTTS2.5", "mimo_tts"]
    assert match_partial_name(names, "indextts2.5", lambda n: n) == "IndexTTS2.5"
    assert match_partial_name(names, "IndexTTS2.5", lambda n: n) == "IndexTTS2.5"
    assert match_partial_name(names, "indextts2", lambda n: n) == "IndexTTS2"
    assert match_partial_name(names, "indextts", lambda n: n) == "IndexTTS2"
