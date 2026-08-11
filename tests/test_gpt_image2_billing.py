"""GPT-Image-2 动态计价:token 计算/积分换算/estimate_cost 钩子"""

import pytest

from RH_ComfyUI.models.image.defs import BananaProDef, GptImage2Def
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.gpt_image2_billing import (
    POINTS_PER_MILLION_TOKENS,
    resolve_dimensions,
    resolve_size_string,
    calculate_image_points,
    calculate_image_tokens,
    estimate_gpt_image2_points,
)

# ── 常量 ──


def test_points_per_million_tokens_constant():
    """210 元 / 1M tokens,1 元 = 100 积分 → 21_000 积分 / 1M tokens"""
    assert POINTS_PER_MILLION_TOKENS == 21_000


# ── 尺寸映射 ──


@pytest.mark.parametrize(
    "ratio,image_size,expected",
    [
        # 与 _RATIO_SIZE_MAP 真源一致(计费 = 实际 size)
        ("1:1", "1K", (1024, 1024)),
        ("1:1", "2K", (2560, 2560)),
        ("1:1", "4K", (2880, 2880)),  # 像素上限封顶,不能 3840x3840
        ("16:9", "1K", (1792, 1008)),
        ("16:9", "2K", (2560, 1440)),
        ("16:9", "4K", (3840, 2160)),
        ("9:16", "2K", (1440, 2560)),
        ("9:16", "4K", (2160, 3840)),
        ("4:3", "2K", (2560, 1920)),
        ("4:3", "4K", (3264, 2448)),
        ("3:4", "2K", (1920, 2560)),
        ("3:4", "4K", (2448, 3264)),
        ("3:2", "1K", (1536, 1024)),
        ("3:2", "2K", (3072, 2048)),
        ("3:2", "4K", (3504, 2336)),
        ("2:3", "1K", (1024, 1536)),
        ("2:3", "2K", (2048, 3072)),
        ("2:3", "4K", (2336, 3504)),
        ("2:1", "1K", (1152, 576)),
        ("2:1", "2K", (2560, 1280)),
        ("2:1", "4K", (3840, 1920)),
        ("1:2", "1K", (576, 1152)),
        ("1:2", "2K", (1280, 2560)),
        ("1:2", "4K", (1920, 3840)),
        ("21:9", "4K", (3808, 1632)),
        # 默认/回落
        ("auto", "2K", (1024, 1024)),  # auto → 默认尺寸(估价)
        (None, "2K", (1024, 1024)),
        ("1:1", None, (2560, 2560)),  # size 缺失 → 2K
        ("1:1", "9K", (2560, 2560)),  # 非法档 → 2K
    ],
)
def test_resolve_dimensions(ratio, image_size, expected):
    assert resolve_dimensions(ratio, image_size) == expected


def test_resolve_size_string_matches_api_and_includes_1_2():
    """计费 size 字符串须与 GPTImage2API / openai 生图共用;含 1:2/2:1。"""
    from RH_ComfyUI.utils.backends.gpt_image2.api import GPTImage2API

    assert resolve_size_string("2:1", "2K") == "2560x1280"
    assert resolve_size_string("1:2", "2K") == "1280x2560"
    assert resolve_size_string("auto", "2K") == "auto"
    assert GPTImage2API.resolve_size("1:2", "4K") == resolve_size_string("1:2", "4K")
    assert GPTImage2API.resolve_size("16:9", "2K") == "2560x1440"


# ── token 计算 ──


def test_calculate_image_tokens_known_value():
    """手工验算 1024x1024 / medium:
    quality_axis_factor = 48
    long_edge = 1024, short_edge = 1024
    short_axis_factor = (2*48*1024 + 1024) // (2*1024) = (98304+1024)//2048 = 48
    tokens = (48 * 48 * (2_000_000 + 1_048_576) + 4_000_000 - 1) // 4_000_000
          = (2304 * 3048576 + 3999999) // 4_000_000
          = 7023925248 + 3999999 = 7027925247 // 4_000_000 = 1756
    """
    assert calculate_image_tokens("medium", 1024, 1024) == 1756


def test_quality_ordering():
    """同尺寸下 high > medium > low"""
    low = calculate_image_tokens("low", 2048, 2048)
    med = calculate_image_tokens("medium", 2048, 2048)
    high = calculate_image_tokens("high", 2048, 2048)
    assert low < med < high


def test_size_ordering_same_ratio():
    """同 quality + 同 aspect ratio 下,像素面积越大 token 越多。

    取 1:1 档 (1024x1024 → 2048x2048),面积 4x,token 近似 4x。
    跨比例(如 1:1 vs 16:9)的 short_axis_factor 不同,不保证单调。
    """
    s1k = calculate_image_tokens("medium", 1024, 1024)
    s2k = calculate_image_tokens("medium", 2048, 2048)
    assert s1k < s2k


# ── 积分换算 ──


def test_calculate_image_points_minimum():
    """最低 1 积分"""
    # low + 1K 极小 token
    pts = calculate_image_points("low", 1024, 1024)
    assert pts >= 1


def test_calculate_image_points_proportional():
    """积分与 token 数成正比:2K medium 积分 ≈ 2× 1K medium(同 quality 下面积 4x,token ~4x)"""
    pts_1k = calculate_image_points("medium", 1024, 1024)
    pts_2k = calculate_image_points("medium", 2048, 2048)
    # 2K 面积 4x,token 约 2x(因 offset),积分也应约 2x
    assert pts_2k > pts_1k


# ── estimate_gpt_image2_points 入口 ──


def test_estimate_defaults():
    """参数全缺 → medium + 1024x1024"""
    assert estimate_gpt_image2_points(None, None, None) == calculate_image_points("medium", 1024, 1024)


def test_estimate_auto_ratio():
    """ratio=auto → 1024x1024"""
    assert estimate_gpt_image2_points("high", "auto", "4K") == calculate_image_points("high", 1024, 1024)


def test_estimate_explicit():
    """显式参数(16:9 2K = 2560x1440,与真源表一致)"""
    assert estimate_gpt_image2_points("low", "16:9", "2K") == calculate_image_points("low", 2560, 1440)


# ── 模型 estimate_cost 钩子 ──


def _make_request(ratio=None, **params) -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.IMAGE, prompt="test", ratio=ratio, params=params)


def test_gpt_image2_estimate_cost_dynamic():
    """GptImage2Def.estimate_cost 走动态计费,不固定返回 point_cost"""
    m = GptImage2Def()
    # 默认参数(无 params) → medium + 1024x1024
    req_default = _make_request()
    cost_default = m.estimate_cost(req_default)
    assert cost_default == estimate_gpt_image2_points("medium", None, None)

    # 高分辨率高质量应比默认贵
    req_high = _make_request(ratio="1:1", quality="high", image_size="4K")
    cost_high = m.estimate_cost(req_high)
    assert cost_high > cost_default

    # 低分辨率低质量应比默认便宜(或相等)
    req_low = _make_request(ratio="1:1", quality="low", image_size="1K")
    cost_low = m.estimate_cost(req_low)
    assert cost_low < cost_default


def test_banana_pro_estimate_cost_independent():
    """BananaProDef 已独立计费(不再与 GptImage2Def 共享计费逻辑)。

    独立计费规则:输入 0.0011 美元/张 + 输出 120 美元/1M tokens 按分辨率分档。
    """
    from RH_ComfyUI.utils.mappers.banana_pro_billing import estimate_banana_pro_points

    m = BananaProDef()
    req = _make_request(ratio="16:9", quality="medium", image_size="2K")
    # 无输入图片 + 2K 档 → 独立计费结果
    assert m.estimate_cost(req) == estimate_banana_pro_points(0, "2K")


def test_gpt_image2_estimate_cost_never_below_minimum():
    """任何合法参数组合积分 ≥ 1"""
    m = GptImage2Def()
    for q in ("low", "medium", "high"):
        for ratio in ("auto", "1:1", "16:9", "9:16"):
            for sz in ("1K", "2K", "4K"):
                req = _make_request(ratio=ratio, quality=q, image_size=sz)
                assert m.estimate_cost(req) >= 1
