"""模型 input_schema 与实际能力一致性 — 参考图端口/上限/纯文本模型拒图"""

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.models.image.defs import (
    AnimaDef,
    Banana2Def,
    Qwen2512Def,
    BananaProDef,
    GptImage2Def,
    MinimaxImage01Def,
)
from RH_ComfyUI.models.video.defs import (
    Wan30Def,
    MiniMaxH3Def,
    Seedance2Def,
    Seedance25Def,
    Seedance2FastDef,
    Seedance2MiniDef,
    Seedance15ProDef,
    Wan22VideogenDef,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest


def test_banana2_accepts_14_images():
    m = Banana2Def()
    assert m.supports_edit is True
    assert m.max_input_images == 14


def test_banana_pro_exposes_nbp_image_size_tiers():
    # NBP 档位事实(上游 1K/2K/4K,无 512)
    node = BananaProDef.node_def()
    size = node.inputs.get("image_size")
    assert size is not None, "banana_pro 缺少 image_size 尺寸档端口"
    assert size.values == ["1K", "2K", "4K"]
    assert size.default == "2K"


@pytest.mark.parametrize("cls", [AnimaDef, MinimaxImage01Def, Qwen2512Def])
def test_text_only_image_models_have_no_image_port_and_reject_images(cls):
    m = cls()
    assert m.supports_edit is False
    assert "images" not in m.node.inputs and "image" not in m.node.inputs
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", images=[b"img"])
    with pytest.raises(ValidationError):
        m.validate(req)


@pytest.mark.parametrize("cls", [Seedance15ProDef, Seedance2FastDef, Seedance25Def])
def test_seedance_variants_declare_media_ports(cls):
    # 模型 supported_shapes 含 图生/多模态,input_schema 必须同步声明媒体端口
    node = cls.node_def()
    for port in ("images", "video_refs", "audio_refs", "frame_mode"):
        assert port in node.inputs, f"{cls.__name__} 缺少 {port} 端口"


def test_wan30_declares_seedance2_like_ports_plus_file():
    node = Wan30Def.node_def()
    for port in ("images", "video_refs", "audio_refs", "frame_mode", "file_url", "link_url"):
        assert port in node.inputs, f"wan3.0 缺少 {port} 端口"
    assert node.inputs["images"].max_items == 10
    assert node.inputs["video_refs"].max_items == 5
    assert node.inputs["audio_refs"].max_items == 5
    assert node.inputs["duration"].minimum == -1
    assert node.inputs["duration"].maximum == 30
    assert node.inputs["resolution"].values == ["480p", "720p", "1080p"]
    assert node.backend_models["dashscope"] == "wan3.0-video"


def test_minimax_h3_declares_media_ports():
    node = MiniMaxH3Def.node_def()
    for port in ("images", "video_refs", "audio_refs", "frame_mode", "task_mode"):
        assert port in node.inputs, f"minimax_h3 缺少 {port} 端口"
    assert node.inputs["task_mode"].values == ["auto", "t2v", "i2v", "first_last", "reference"]
    assert node.inputs["images"].max_items == 9
    assert node.inputs["duration"].minimum == 4
    assert node.inputs["duration"].maximum == 15
    assert node.inputs["resolution"].values == ["768p", "2k"]
    assert node.inputs["ratio"].values == ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]
    assert node.inputs["ratio"].default == "16:9"
    assert "adaptive" not in node.inputs["ratio"].values
    assert "generate_audio" not in node.inputs


@pytest.mark.parametrize(
    "cls",
    [Seedance2Def, Seedance2FastDef, Seedance2MiniDef, Seedance25Def],
)
def test_seedance2_family_hides_camera_fixed(cls):
    """官方 2.x 不支持 camera_fixed;catalog 暴露会让前端出现「固定镜头」。"""
    assert "camera_fixed" not in cls.node_def().inputs


def test_seedance15_keeps_camera_fixed():
    assert "camera_fixed" in Seedance15ProDef.node_def().inputs


def test_seedance25_declares_task_mode_and_output_format():
    node = Seedance25Def.node_def()
    assert "task_mode" in node.inputs
    assert "output_format" in node.inputs
    assert "omni_reference_task_type" in node.inputs
    assert node.inputs["images"].max_items == 30
    assert node.inputs["duration"].maximum == 30


@pytest.mark.parametrize("cls", [BananaProDef, GptImage2Def, MinimaxImage01Def])
def test_ratio_based_image_models_expose_ratio_not_wh(cls):
    # 这些模型的真实请求参数是宽高比(aspect_ratio / aspect_ratio+image_size→size),
    # 不吃宽高像素 —— schema 必须暴露 ratio 枚举,不得假装接受 width/height。
    # (与 test_gemini_image 对 banana2/banana1 的同类断言口径一致)
    from RH_ComfyUI.utils.mappers.gpt_image2_billing import _RATIO_SIZE_MAP

    node = cls.node_def()
    assert "width" not in node.inputs and "height" not in node.inputs
    ratio = node.inputs.get("ratio")
    assert ratio is not None and ratio.values, f"{cls.__name__} 缺少 ratio 枚举端口"
    # ratio 枚举值(除 "auto" 外)必须全部在映射表内
    ratio_values = set(ratio.values) - {"auto"}
    assert ratio_values <= set(_RATIO_SIZE_MAP), f"{cls.__name__} ratio 枚举超出上游支持范围"
    assert ratio.default in ratio.values


@pytest.mark.parametrize("cls", [BananaProDef, GptImage2Def])
def test_gpt_image2_family_exposes_all_billing_ratios_including_1_2(cls):
    """计费/像素真源表中的 ratio(含 1:2、2:1)必须完整暴露到 /models schema。"""
    from RH_ComfyUI.utils.mappers.gpt_image2_billing import _RATIO_SIZE_MAP

    node = cls.node_def()
    ratio = node.inputs.get("ratio")
    assert ratio is not None
    exposed = set(ratio.values) - {"auto"}
    assert exposed == set(_RATIO_SIZE_MAP), (
        f"{cls.__name__} ratio 与 _RATIO_SIZE_MAP 不一致: "
        f"missing={set(_RATIO_SIZE_MAP) - exposed} extra={exposed - set(_RATIO_SIZE_MAP)}"
    )
    assert "1:2" in ratio.values and "2:1" in ratio.values


@pytest.mark.parametrize("cls", [AnimaDef, Qwen2512Def, Wan22VideogenDef])
def test_pixel_based_models_keep_wh(cls):
    # ComfyUI 工作流 / rh_app 是真实消费像素宽高的,width/height 端口保留
    node = cls.node_def()
    assert "width" in node.inputs and "height" in node.inputs
