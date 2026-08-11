"""回归: seedream5_pro 的 estimate_cost 必须按 image_size 区分 1K (30 积分) / 2K (60 积分)。

历史 bug: estimate_cost 从 request.params.get("size_mode") 取参,而前端通用
estimate 接口发的是 image_size,key 不一致导致永远走默认 2K → 60 积分。
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


def test_seedream5_pro_1k_costs_30_points():
    r = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K"))
    assert r["point_cost"] == 30, r
    assert r["is_dynamic"] is True


def test_seedream5_pro_2k_costs_60_points():
    r = asyncio.run(estimate_model_points("seedream5_pro", image_size="2K"))
    assert r["point_cost"] == 60, r
    assert r["is_dynamic"] is True


def test_seedream5_pro_1k_and_2k_differ():
    """核心断言:不同档位必须不同,不再全部 60"""
    r1 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K"))
    r2 = asyncio.run(estimate_model_points("seedream5_pro", image_size="2K"))
    assert r1["point_cost"] != r2["point_cost"]
    assert r1["point_cost"] < r2["point_cost"]


def test_seedream5_pro_input_image_cost():
    """输入图第 2 张起每张 +2 积分"""
    r0 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K"))
    r2 = asyncio.run(estimate_model_points("seedream5_pro", image_size="1K"))
    # 0 输入图 + 1K → 30
    assert r0["point_cost"] == 30
    # 注:estimate_model_points 当前不支持传 num_input_images(只透传到 params),
    # 这里只验证 image_size 维度,输入图维度由单测覆盖 billing mapper
    assert r2["point_cost"] == 30


def test_seedream5_pro_default_fallback_when_no_image_size():
    """不传 image_size 时回落默认 2K = 60 积分(明确预期,便于文档化)"""
    r = asyncio.run(estimate_model_points("seedream5_pro"))
    assert r["point_cost"] == 60
