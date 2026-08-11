"""回归: estimate_model_points 必须把 num_input_images 反映到积分。

历史 bug: 后端创建 GenerationRequest 时没传 images 字段,estimate_cost 读
len(request.images) 永远 0,导致 seedream5_pro / banana_pro 这类按图计费
的模型在前端预览时永远按 0 张图算,实际生成时才暴露差异。
"""

from __future__ import annotations

import asyncio

import pytest

from RH_ComfyUI.models import discover_builtin_models
from RH_ComfyUI.rh_models.api import estimate_model_points
from RH_ComfyUI.utils.backends import init_backends
from RH_ComfyUI.core.routing.registry import model_registry


@pytest.fixture(autouse=True)
def _setup():
    init_backends()
    discover_builtin_models()
    yield
    model_registry.clear()


def test_seedream5_pro_input_image_count_charges():
    """seedream5_pro: 首张免费,第 2 张起每张 +2 积分"""
    r0 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K", num_input_images=0))
    r1 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K", num_input_images=1))
    r2 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K", num_input_images=2))
    r5 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K", num_input_images=5))

    # 1K 输出 = 30 积分
    # 0/1 输入 → 不加(首张免费)
    assert r0["point_cost"] == 30
    assert r1["point_cost"] == 30
    # 2 输入 → 30 + 2 = 32
    assert r2["point_cost"] == 32, r2
    # 5 输入 → 30 + 4*2 = 38
    assert r5["point_cost"] == 38, r5


def test_seedream5_pro_input_count_echoed_in_params():
    """返回 params.num_input_images 反映请求值"""
    r = asyncio.run(estimate_model_points("seedream5_pro", num_input_images=3))
    assert r["params"]["num_input_images"] == 3


def test_banana_pro_input_image_count_charges():
    """banana_pro: 按图计费,每张 0.0011 美元 ≈ 输入图积分(具体见 mapper)"""
    r0 = asyncio.run(estimate_model_points("banana_pro", image_size="2K", num_input_images=0))
    r3 = asyncio.run(estimate_model_points("banana_pro", image_size="2K", num_input_images=3))
    # 至少 r3 > r0 —— 多图应该更贵(无论计费曲线具体值)
    assert r3["point_cost"] > r0["point_cost"], (
        f"banana_pro num_input_images 不生效:r0={r0['point_cost']}, r3={r3['point_cost']}"
    )


def test_num_input_images_default_zero():
    """不传 num_input_images 等价于 num_input_images=0(向后兼容)"""
    r = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K"))
    assert r["point_cost"] == 30
    assert r["params"]["num_input_images"] == 0


def test_gpt_image2_unaffected_by_input_count():
    """gpt-image-2 不按输入图收费,num_input_images 应被忽略"""
    r0 = asyncio.run(estimate_model_points("gpt-image-2", image_size="1K", quality="low", num_input_images=0))
    r5 = asyncio.run(estimate_model_points("gpt-image-2", image_size="1K", quality="low", num_input_images=5))
    assert r0["point_cost"] == r5["point_cost"], (
        f"gpt-image-2 不应受 num_input_images 影响,但发现差异: {r0['point_cost']} vs {r5['point_cost']}"
    )
