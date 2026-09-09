"""回归: banana_pro schema 不应暴露 quality 字段。

历史 bug: banana_pro 的 input_schema 声明了 quality 枚举,前端按 schema 渲染了
quality 控件。但 banana_pro 官方计费曲线不区分 quality 档位(只按 image_size 分档),
estimate_cost 不读 quality —— 用户切换 quality 时积分不变,造成"积分 bug"误判。

修复:从 BananaProDef.node_def() 移除 quality 字段。前端不再渲染,契约清晰。
"""

from __future__ import annotations

from RH_ComfyUI.models.image.defs import BananaProDef


def test_banana_pro_schema_has_no_quality():
    """banana_pro 的 input_schema 不应包含 quality"""
    inputs = BananaProDef.node_def().inputs
    assert "quality" not in inputs, (
        f"banana_pro schema 不应暴露 quality(官方计费曲线不区分 quality)。实际 inputs: {list(inputs.keys())}"
    )


def test_banana_pro_schema_has_ratio_and_image_size():
    """ratio 和 image_size 仍应保留(计费曲线按 image_size 分档,ratio 用于实际生成)"""
    inputs = BananaProDef.node_def().inputs
    assert "ratio" in inputs
    assert "image_size" in inputs


def test_gpt_image2_still_has_quality():
    """回归保护:gpt-image-2 的 quality 字段不应被误删(它有计费差异)"""
    from RH_ComfyUI.models.image.defs import GptImage2Def, GptImage25FlareDef, GptImage25SunburstDef
    from RH_ComfyUI.utils.mappers.gpt_image2_params import GPT_IMAGE2_QUALITIES, GPT_IMAGE25_QUALITIES

    gpt = GptImage2Def.node_def().inputs
    assert "quality" in gpt, "gpt-image-2 计费按 quality_factor 分档,quality 字段必须保留"
    assert gpt["quality"].values == list(GPT_IMAGE2_QUALITIES)
    for cls in (GptImage25FlareDef, GptImage25SunburstDef):
        values = cls.node_def().inputs["quality"].values
        assert values == list(GPT_IMAGE25_QUALITIES)


def test_gpt_image_family_schema_has_background_and_output_format():
    from RH_ComfyUI.models.image.defs import GptImage2Def, GptImage25FlareDef, GptImage25SunburstDef

    for cls in (GptImage2Def, GptImage25SunburstDef, GptImage25FlareDef):
        inputs = cls.node_def().inputs
        bg = inputs["background"]
        fmt = inputs["output_format"]
        assert bg.default == "auto"
        assert bg.values == ["transparent", "opaque", "auto"]
        assert bg.value_titles == {"transparent": "透明", "opaque": "不透明", "auto": "自动"}
        assert fmt.default == "png"
        assert fmt.values == ["png", "jpeg"]
        assert fmt.value_titles == {"png": "PNG", "jpeg": "JPEG"}


def test_banana_pro_schema_has_no_background_or_output_format():
    inputs = BananaProDef.node_def().inputs
    assert "background" not in inputs
    assert "output_format" not in inputs
