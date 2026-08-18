"""MiniMax H3 计费:768P 0.50 元/秒,2K 0.80 元/秒,含输入视频秒。"""

from RH_ComfyUI.utils.mappers.minimax_h3_billing import (
    estimate_minimax_h3_points,
    calculate_minimax_h3_points,
)


def test_768p_4s_is_200():
    assert calculate_minimax_h3_points("768p", 4) == 200


def test_2k_5s_is_400():
    assert calculate_minimax_h3_points("2k", 5) == 400


def test_2k_15s_plus_input_15s():
    # 0.80 * (15+15) = 24 元 = 2400
    assert calculate_minimax_h3_points("2k", 15, input_video_duration=15) == 2400


def test_resolution_aliases():
    assert calculate_minimax_h3_points("768P", 4) == 200
    assert calculate_minimax_h3_points("2K", 5) == 400


def test_estimate_uses_video_refs_count():
    # 无显式 input 时长时,每段参考按 5s 估
    assert estimate_minimax_h3_points("2k", 5, video_refs=[object(), object()]) == calculate_minimax_h3_points(
        "2k", 5, input_video_duration=10
    )


def test_min_one_point():
    assert calculate_minimax_h3_points("768p", 4) >= 1
