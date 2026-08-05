"""HappyHorse 积分计价"""

from RH_ComfyUI.utils.mappers.happyhorse_billing import (
    calculate_happyhorse_points,
    estimate_happyhorse_points,
)


def test_points_scale_with_duration():
    short = calculate_happyhorse_points("720p", 3)
    long = calculate_happyhorse_points("720p", 15)
    assert short < long
    assert short >= 1


def test_points_scale_with_resolution():
    low = calculate_happyhorse_points("480p", 5)
    mid = calculate_happyhorse_points("720p", 5)
    high = calculate_happyhorse_points("1080p", 5)
    assert low < mid < high


def test_estimate_alias():
    assert estimate_happyhorse_points("720p", 5) == calculate_happyhorse_points("720p", 5)


def test_defaults():
    # 默认 720p × 5s = 100 积分/s × 5 = 500
    assert calculate_happyhorse_points(None, None) == 500
