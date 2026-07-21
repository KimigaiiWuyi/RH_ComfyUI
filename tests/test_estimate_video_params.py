"""回归: estimate_model_points 必须支持视频参数 (resolution / duration / generate_audio)。

历史 bug: 后端 estimate_model_points 签名只声明 ratio/image_size/quality,
前端 estimateParams 给 seedance2 等视频模型发的 duration/resolution 被 FastAPI
直接 422 拒绝,导致前端拿不到正确的预览积分。
"""

from __future__ import annotations

import asyncio

import pytest

from RH_ComfyUI.rh_models.api import estimate_model_points
from RH_ComfyUI.models import discover_builtin_models
from RH_ComfyUI.utils.backends import init_backends
from RH_ComfyUI.core.routing.registry import model_registry


@pytest.fixture(autouse=True)
def _setup():
    init_backends()
    discover_builtin_models()
    yield
    model_registry.clear()


def test_seedance2_accepts_resolution_and_duration():
    """不报 TypeError 就算过(从前端发过来的 resolution+duration 必须被接受)"""
    r = asyncio.run(estimate_model_points("seedance2", resolution="720p", duration=5))
    assert r["point_cost"] > 0


def test_seedance2_resolution_changes_cost():
    """同 duration 不同 resolution → 不同积分"""
    r_low = asyncio.run(estimate_model_points("seedance2", resolution="480p", duration=5))
    r_high = asyncio.run(estimate_model_points("seedance2", resolution="1080p", duration=5))
    assert r_high["point_cost"] > r_low["point_cost"]


def test_seedance2_duration_changes_cost():
    """同 resolution 不同 duration → 不同积分"""
    r_short = asyncio.run(estimate_model_points("seedance2", resolution="720p", duration=4))
    r_long = asyncio.run(estimate_model_points("seedance2", resolution="720p", duration=15))
    assert r_long["point_cost"] > r_short["point_cost"]


def test_seedance2_video_refs_increases_cost():
    """有输入视频参考 → 比无输入贵(费率有差异)"""
    r_none = asyncio.run(estimate_model_points("seedance2", resolution="720p", duration=5, num_video_refs=0))
    r_some = asyncio.run(estimate_model_points("seedance2", resolution="720p", duration=5, num_video_refs=1))
    assert r_some["point_cost"] > r_none["point_cost"], (
        f"seedance2 输入视频参考应该更贵,但 {r_none['point_cost']} >= {r_some['point_cost']}"
    )


def test_seedance15_pro_generate_audio_changes_cost():
    """Seedance 1.5 Pro: 有声 vs 无声应不同(有声 16 元/M, 无声 8 元/M)"""
    r_silent = asyncio.run(
        estimate_model_points("seedance15_pro", resolution="720p", duration=5, generate_audio=False)
    )
    r_audio = asyncio.run(
        estimate_model_points("seedance15_pro", resolution="720p", duration=5, generate_audio=True)
    )
    assert r_audio["point_cost"] > r_silent["point_cost"], (
        f"有声应该比无声贵,但 {r_audio['point_cost']} <= {r_silent['point_cost']}"
    )


def test_estimate_video_params_echoed_in_response():
    """新参数应回显在 response.params 里,前端可观测"""
    r = asyncio.run(
        estimate_model_points(
            "seedance2", resolution="1080p", duration=10, generate_audio=False, num_video_refs=2
        )
    )
    assert r["params"]["resolution"] == "1080p"
    assert r["params"]["duration"] == 10
    assert r["params"]["generate_audio"] is False
    assert r["params"]["num_video_refs"] == 2


def test_image_model_ignores_video_params_gracefully():
    """图片模型收到 video 参数应忽略,不应报错"""
    r = asyncio.run(
        estimate_model_points(
            "gpt-image-2", ratio="1:1", image_size="4K", quality="high",
            resolution="1080p", duration=10,  # 视频参数对图片模型无意义
        )
    )
    assert r["point_cost"] > 0
    # 验证图片模型只按图片参数算,不受视频参数影响
    r_no_video = asyncio.run(
        estimate_model_points("gpt-image-2", ratio="1:1", image_size="4K", quality="high")
    )
    assert r["point_cost"] == r_no_video["point_cost"]


def test_wan22_videogen_accepts_video_params():
    """wan2.2_videogen 也必须支持 resolution+duration"""
    r = asyncio.run(estimate_model_points("wan2.2_videogen", resolution="720p", duration=5))
    assert r["point_cost"] > 0