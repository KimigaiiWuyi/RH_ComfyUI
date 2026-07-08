"""模型 input_schema 与实际能力一致性 — 参考图端口/上限/纯文本模型拒图"""

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.models.image.defs import (
    AnimaDef,
    Banana2Def,
    Qwen2512Def,
    MinimaxImage01Def,
)
from RH_ComfyUI.models.video.defs import Seedance2FastDef, Seedance15ProDef
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest


def test_banana2_accepts_14_images():
    m = Banana2Def()
    assert m.supports_edit is True
    assert m.max_input_images == 14


@pytest.mark.parametrize("cls", [AnimaDef, MinimaxImage01Def, Qwen2512Def])
def test_text_only_image_models_have_no_image_port_and_reject_images(cls):
    m = cls()
    assert m.supports_edit is False
    assert "images" not in m.node.inputs and "image" not in m.node.inputs
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", images=[b"img"])
    with pytest.raises(ValidationError):
        m.validate(req)


@pytest.mark.parametrize("cls", [Seedance15ProDef, Seedance2FastDef])
def test_seedance_variants_declare_media_ports(cls):
    # 模型 supported_shapes 含 图生/多模态,input_schema 必须同步声明媒体端口
    node = cls.node_def()
    for port in ("images", "video_refs", "audio_refs", "frame_mode"):
        assert port in node.inputs, f"{cls.__name__} 缺少 {port} 端口"
