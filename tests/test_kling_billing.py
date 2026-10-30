"""可灵积分:人民币向上取整,视频按秒,图片按张。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from RH_ComfyUI.utils.backends.kling.billing import (
    image_yuan,
    video_yuan,
    cny_to_points,
    image_point_bounds,
    video_point_bounds,
)


def test_cny_rounds_up_and_keeps_at_least_one_point() -> None:
    assert cny_to_points(Decimal("0.2")) == 3
    assert cny_to_points(Decimal("0.1")) == 2
    assert cny_to_points(Decimal("0")) == 1


def test_video_examples_match_published_rates() -> None:
    turbo = video_yuan("kling-3.0-turbo", resolution="720p", seconds=5, sound="always", ref_video=False)
    assert turbo == Decimal("4.0")
    assert cny_to_points(turbo) == 56

    standard = video_yuan("kling-3.0", resolution="720p", seconds=5, sound="native", ref_video=False, omni=False)
    omni = video_yuan("kling-3.0", resolution="720p", seconds=5, sound="native", ref_video=False, omni=True)
    assert cny_to_points(standard) == 63
    assert cny_to_points(omni) == 56
    assert standard != omni

    four_k = video_yuan("kling-3.0", resolution="4k", seconds=5, sound="silent", ref_video=False)
    assert cny_to_points(four_k) == 210

    referred = video_yuan("kling-3.0", resolution="720p", seconds=5, sound="silent", ref_video=True, omni=True)
    assert referred == Decimal("4.5")


def test_v40_rates_follow_input_video_duration() -> None:
    plain = video_yuan("kling-4.0", resolution="720p", seconds=5, sound="", ref_video=False)
    assert plain == Decimal("5.0")
    hi = video_yuan("kling-4.0", resolution="1080p", seconds=5, sound="", ref_video=False)
    assert hi == Decimal("7.5")
    # 输入短于输出:按输出×2 秒计
    short = video_yuan("kling-4.0", resolution="720p", seconds=5, sound="", ref_video=True, input_seconds=4)
    assert short == Decimal("6.0")
    # 输入长于输出:按(输入+输出)秒计
    long_in = video_yuan("kling-4.0", resolution="1080p", seconds=10, sound="", ref_video=True, input_seconds=20)
    assert long_in == Decimal("27.0")


def test_image_examples_match_published_rates() -> None:
    assert cny_to_points(image_yuan("kling-image-3.0", tier="t2i", resolution="1k", count=1)) == 3
    assert cny_to_points(image_yuan("kling-image-3.0", tier="omni", resolution="4k", count=1)) == 6
    assert cny_to_points(image_yuan("kling-image-2.1", tier="t2i", resolution="1k", count=1)) == 2
    assert cny_to_points(image_yuan("kling-image-2.1", tier="i2i", resolution="2k", count=1)) == 3
    assert cny_to_points(image_yuan("kling-image-2.1", tier="multi", resolution="1k", count=1)) == 6
    assert cny_to_points(image_yuan("kling-image-common", tier="outpaint", resolution="1k", count=1)) == 3
    assert cny_to_points(image_yuan("kling-image-common", tier="subject", resolution="1k", count=9)) == 7


def test_point_bounds_cover_each_catalog_model() -> None:
    assert video_point_bounds("kling-4.0") == (42, 756)
    assert video_point_bounds("kling-3.0-turbo") == (34, 210)
    assert video_point_bounds("kling-3.0") == (26, 630)
    assert video_point_bounds("kling-o1") == (26, 168)
    assert video_point_bounds("kling-2.6") == (21, 168)
    assert video_point_bounds("kling-2.5-turbo") == (21, 70)
    assert image_point_bounds("kling-image-3.0") == (3, 51)
    assert image_point_bounds("kling-image-o1") == (3, 26)
    assert image_point_bounds("kling-image-2.1") == (2, 51)
    assert image_point_bounds("kling-image-common") == (3, 26)
    for name in ("kling-4.0", "kling-3.0-turbo", "kling-3.0", "kling-o1", "kling-2.6", "kling-2.5-turbo"):
        low, high = video_point_bounds(name)
        assert low < high
    for name in ("kling-image-3.0", "kling-image-o1", "kling-image-2.1", "kling-image-common"):
        low, high = image_point_bounds(name)
        assert low < high


def test_unknown_model_or_resolution_is_rejected() -> None:
    with pytest.raises(ValueError):
        video_yuan("kling-unknown", resolution="720p", seconds=5, sound="silent", ref_video=False)
    with pytest.raises(ValueError):
        video_yuan("kling-o1", resolution="4k", seconds=5, sound="silent", ref_video=False)
    with pytest.raises(ValueError):
        image_yuan("kling-image-3.0", tier="omni", resolution="1k", count=0)
