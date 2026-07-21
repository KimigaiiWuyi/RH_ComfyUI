"""GET /api/RH_ComfyUI/models/estimate — 动态积分估算接口"""

import asyncio

import pytest

from RH_ComfyUI.rh_models.api import estimate_model_points


@pytest.fixture(autouse=True)
def _register_models():
    """确保模型注册表中加载了全部内置模型"""
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.core.routing.registry import model_registry

    init_backends()
    discover_builtin_models()
    yield
    model_registry.clear()


def test_estimate_gpt_image2_dynamic():
    """gpt-image-2 走动态计费,不同参数返回不同积分"""
    r_default = asyncio.run(estimate_model_points("gpt-image-2"))
    assert r_default["model"] == "gpt-image-2"
    assert r_default["point_cost"] > 0

    r_high = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="1:1", image_size="4K", quality="high")
    )
    assert r_high["point_cost"] > r_default["point_cost"]
    assert r_high["is_dynamic"] is True

    r_low = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="1:1", image_size="1K", quality="low")
    )
    assert r_low["point_cost"] < r_default["point_cost"]


def test_estimate_gpt_image2_auto_ratio():
    """ratio=auto → 1024x1024 估算,与显式 1:1 1K 同尺寸应相同"""
    r_auto = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="auto", image_size="4K", quality="high")
    )
    # auto 回落 1024x1024,与 1:1 1K (也是 1024x1024) 同尺寸
    r_1k = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="1:1", image_size="1K", quality="high")
    )
    assert r_auto["point_cost"] == r_1k["point_cost"]
    # 但 auto(1024x1024) 与 4K 1:1 (3840x2160) 应不同
    r_4k = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="1:1", image_size="4K", quality="high")
    )
    assert r_auto["point_cost"] != r_4k["point_cost"]


def test_estimate_banana_pro_matches_gpt_image2():
    """banana_pro 与 gpt-image-2 共享计费逻辑"""
    r_bp = asyncio.run(
        estimate_model_points("banana_pro", ratio="16:9", image_size="2K", quality="medium")
    )
    r_gpt = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="16:9", image_size="2K", quality="medium")
    )
    assert r_bp["point_cost"] == r_gpt["point_cost"]
    assert r_bp["is_dynamic"] is True


def test_estimate_static_model_fallback():
    """未覆盖 estimate_cost 的模型返回静态 point_cost"""
    r = asyncio.run(
        estimate_model_points("qwen_2512", ratio="1:1", image_size="4K", quality="high")
    )
    assert r["model"] == "qwen_2512"
    assert r["point_cost"] == 2  # 静态值
    assert r["is_dynamic"] is False


def test_estimate_unknown_model():
    """未知模型返回 0 积分 + error"""
    r = asyncio.run(estimate_model_points("nonexistent_model"))
    assert r["point_cost"] == 0
    assert "error" in r


def test_estimate_params_normalized():
    """返回的 params 与传入一致"""
    r = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="9:16", image_size="2K", quality="low")
    )
    assert r["params"] == {
        "ratio": "9:16",
        "image_size": "2K",
        "quality": "low",
        "resolution": None,
        "duration": None,
        "generate_audio": None,
        "num_input_images": 0,
        "num_video_refs": 0,
    }


def test_estimate_partial_params():
    """只传部分参数,缺失的不在 params 中"""
    r = asyncio.run(estimate_model_points("gpt-image-2", quality="high"))
    assert r["params"] == {
        "ratio": None,
        "image_size": None,
        "quality": "high",
        "resolution": None,
        "duration": None,
        "generate_audio": None,
        "num_input_images": 0,
        "num_video_refs": 0,
    }
    r_default = asyncio.run(estimate_model_points("gpt-image-2"))
    assert r["point_cost"] > r_default["point_cost"]
