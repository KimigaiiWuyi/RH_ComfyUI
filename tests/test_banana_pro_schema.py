"""回归: banana_pro schema 不应暴露 quality 字段。

历史 bug: banana_pro 的 input_schema 声明了 quality 枚举,前端按 schema 渲染了
quality 控件。但 banana_pro 官方计费曲线不区分 quality 档位(只按 image_size 分档),
estimate_cost 不读 quality —— 用户切换 quality 时积分不变,造成"积分 bug"误判。

修复:从 BananaProDef.node_def() 移除 quality 字段。前端不再渲染,契约清晰。
"""

from __future__ import annotations

import pytest

from RH_ComfyUI.models.image.defs import BananaProDef


def test_banana_pro_schema_has_no_quality():
    """banana_pro 的 input_schema 不应包含 quality"""
    inputs = BananaProDef.node_def().inputs
    assert "quality" not in inputs, (
        f"banana_pro schema 不应暴露 quality(官方计费曲线不区分 quality)。"
        f"实际 inputs: {list(inputs.keys())}"
    )


def test_banana_pro_schema_has_ratio_and_image_size():
    """ratio 和 image_size 仍应保留(计费曲线按 image_size 分档,ratio 用于实际生成)"""
    inputs = BananaProDef.node_def().inputs
    assert "ratio" in inputs
    assert "image_size" in inputs


def test_gpt_image2_still_has_quality():
    """回归保护:gpt-image-2 的 quality 字段不应被误删(它有计费差异)"""
    from RH_ComfyUI.models.image.defs import GptImage2Def

    inputs = GptImage2Def.node_def().inputs
    assert "quality" in inputs, (
        "gpt-image-2 计费按 quality_factor 分档,quality 字段必须保留"
    )