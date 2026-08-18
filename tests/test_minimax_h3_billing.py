"""MiniMax H3 计费:768P 0.50 元/秒,2K 0.80 元/秒;输入视频同档按秒;图 5 张内免费。"""

from RH_ComfyUI.utils.mappers.minimax_h3_billing import (
    estimate_minimax_h3_points,
    calculate_minimax_h3_points,
)


def test_768p_4s_is_200():
    assert calculate_minimax_h3_points("768p", 4) == 200


def test_2k_5s_is_400():
    assert calculate_minimax_h3_points("2k", 5) == 400


def test_768p_input_10s_output_10s_is_10_yuan():
    # 用户例:768P 输入 10s + 输出 10s = 20s × 0.5 = 10 元 = 1000 积分
    assert calculate_minimax_h3_points("768p", 10, input_video_duration=10) == 1000


def test_2k_15s_plus_input_15s():
    # 0.80 * (15+15) = 24 元 = 2400
    assert calculate_minimax_h3_points("2k", 15, input_video_duration=15) == 2400


def test_first_five_images_free():
    base = calculate_minimax_h3_points("768p", 5)
    assert calculate_minimax_h3_points("768p", 5, num_input_images=0) == base
    assert calculate_minimax_h3_points("768p", 5, num_input_images=5) == base


def test_sixth_image_costs_20_points():
    # 超出免费额度每张 0.2 元 = 20 积分
    base = calculate_minimax_h3_points("2k", 5, num_input_images=5)
    extra = calculate_minimax_h3_points("2k", 5, num_input_images=6)
    assert extra - base == 20


def test_nine_images_charge_four_extras():
    # 9-5=4 张 × 0.2 元 = 80 积分
    base = calculate_minimax_h3_points("2k", 5)
    full = calculate_minimax_h3_points("2k", 5, num_input_images=9)
    assert full - base == 80


def test_resolution_aliases():
    assert calculate_minimax_h3_points("768P", 4) == 200
    assert calculate_minimax_h3_points("2K", 5) == 400


def test_estimate_uses_video_refs_count():
    # 无显式 input 时长时,每段参考按 5s 估
    assert estimate_minimax_h3_points("2k", 5, video_refs=[object(), object()]) == calculate_minimax_h3_points(
        "2k", 5, input_video_duration=10
    )


def test_estimate_uses_images_list():
    assert estimate_minimax_h3_points("2k", 5, images=[b""] * 6) == calculate_minimax_h3_points(
        "2k", 5, num_input_images=6
    )


def test_min_one_point():
    assert calculate_minimax_h3_points("768p", 4) >= 1
