"""回归: _RATIO_SIZE_MAP 每个 cell 必须满足 OpenAI 上游 4 条硬约束 + 比例精确。

历史 bug:早期表里大量 cell 用了 1024x1024 / 2048x2048 / 3840x2160 这些"占位值",
比例与对应 ratio 不一致(2:3 + 2K 给的是 2048x2048 正方形,实际应为 2048x3072),
导致 4K 反便宜、2:3 2K 等于 1K、3:2 2K 等于 16:9 等问题。
"""

from __future__ import annotations

import pytest

from RH_ComfyUI.utils.mappers.gpt_image2_billing import (
    _RATIO_SIZE_MAP,
    resolve_dimensions,
    estimate_gpt_image2_points,
)

# ── 表结构 ──


def test_every_cell_is_a_valid_size():
    """每个 cell 必须满足 OpenAI 上游约束 + 比例精确。"""
    for ratio, tiers in _RATIO_SIZE_MAP.items():
        rw, _, rh = ratio.partition(":")
        r_w, r_h = int(rw), int(rh)
        for tier, size_str in tiers.items():
            w_str, _, h_str = size_str.partition("x")
            w, h = int(w_str), int(h_str)

            # 1. 两边都 16 整除
            assert w % 16 == 0, f"{ratio} {tier}={size_str}: width {w} 不是 16 整除"
            assert h % 16 == 0, f"{ratio} {tier}={size_str}: height {h} 不是 16 整除"

            # 2. max edge ≤ 3840
            assert max(w, h) <= 3840, f"{ratio} {tier}={size_str}: max edge {max(w, h)} > 3840"

            # 3. 比例精确,且方向与 ratio 一致(landscape/portrait)
            assert w * r_h == h * r_w, f"{ratio} {tier}={size_str}: 比例不匹配(期望 {r_w}:{r_h}, 实际 {w}:{h})"
            if r_w > r_h:
                assert w > h, f"{ratio} {tier}={size_str}: 应该是 landscape(w>h) 但 {w}<{h}"
            elif r_w < r_h:
                assert w < h, f"{ratio} {tier}={size_str}: 应该是 portrait(w<h) 但 {w}>{h}"
            else:
                assert w == h, f"{ratio} {tier}={size_str}: 应该是方形但 {w}!={h}"

            # 4. 像素在 [655360, 8294400]
            pixels = w * h
            assert 655_360 <= pixels <= 8_294_400, f"{ratio} {tier}={size_str}: 像素 {pixels} 超出合法范围"


# ── 单调性:每个 ratio 下 4K > 2K > 1K(同 quality) ──


@pytest.mark.parametrize("ratio", list(_RATIO_SIZE_MAP.keys()))
def test_4k_strictly_more_expensive_than_2k(ratio):
    """核心断言:同 ratio + high quality 下,4K 积分必须 > 2K 积分。"""
    high_2k = estimate_gpt_image2_points("high", ratio, "2K")
    high_4k = estimate_gpt_image2_points("high", ratio, "4K")
    assert high_4k > high_2k, f"{ratio}: 4K ({high_4k}) 反而 ≤ 2K ({high_2k})! size 表错误或公式异常。"


@pytest.mark.parametrize("ratio", list(_RATIO_SIZE_MAP.keys()))
@pytest.mark.parametrize("quality", ["low", "medium", "high"])
def test_tier_monotonic_for_all_qualities(ratio, quality):
    """每个 ratio × quality 组合下,1K < 2K < 4K。"""
    pts_1k = estimate_gpt_image2_points(quality, ratio, "1K")
    pts_2k = estimate_gpt_image2_points(quality, ratio, "2K")
    pts_4k = estimate_gpt_image2_points(quality, ratio, "4K")
    assert pts_1k < pts_2k < pts_4k, f"{ratio} {quality}: 积分不单调 1K={pts_1k} < 2K={pts_2k} < 4K={pts_4k}"


# ── 用户报告的具体 case ──


def test_user_case_2_3_2k_vs_4k():
    """用户原始 case:ratio=2:3 image_size=2K vs 4K,4K 必须更贵。"""
    pts_2k = estimate_gpt_image2_points("high", "2:3", "2K")
    pts_4k = estimate_gpt_image2_points("high", "2:3", "4K")
    assert pts_4k > pts_2k
    # 同时确认尺寸表已修正:2K 应该是 2048x3072(不是 2048x2048 正方形)
    assert resolve_dimensions("2:3", "2K") == (2048, 3072)
    assert resolve_dimensions("2:3", "4K") == (2336, 3504)


# ── 表覆盖完整性 ──


def test_all_schema_ratios_have_all_tiers():
    """每个 ratio 必须有 1K/2K/4K 三个 tier(前端 schema 按 tier 渲染)。"""
    for ratio, tiers in _RATIO_SIZE_MAP.items():
        assert "1K" in tiers, f"{ratio} 缺 1K 档"
        assert "2K" in tiers, f"{ratio} 缺 2K 档"
        assert "4K" in tiers, f"{ratio} 缺 4K 档"
