"""万相 3.0 积分计价"""

from RH_ComfyUI.utils.mappers.wan30_billing import (
    estimate_wan30_points,
    calculate_wan30_points,
)


def test_points_scale_with_duration():
    short = calculate_wan30_points("720p", 2)
    long = calculate_wan30_points("720p", 30)
    assert short < long
    assert short == 120  # 0.6 * 2 * 100
    assert long == 1800  # 0.6 * 30 * 100


def test_points_scale_with_resolution():
    low = calculate_wan30_points("480p", 5)
    mid = calculate_wan30_points("720p", 5)
    high = calculate_wan30_points("1080p", 5)
    assert low == 150
    assert mid == 300
    assert high == 600
    assert low < mid < high


def test_auto_duration_uses_15s():
    assert calculate_wan30_points("1080p", -1) == calculate_wan30_points("1080p", 15)


def test_estimate_alias():
    assert estimate_wan30_points("720p", 5) == calculate_wan30_points("720p", 5)


def test_defaults():
    # 默认 1080p × 5s = 1.2 * 5 * 100 = 600
    assert calculate_wan30_points(None, None) == 600
