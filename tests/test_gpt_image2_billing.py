"""GPT-Image-2 动态计价:token 计算/积分换算/estimate_cost 钩子"""

import pytest

from RH_ComfyUI.models.image.defs import BananaProDef, GptImage2Def, GptImage25FlareDef, GptImage25SunburstDef
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.gpt_image2_billing import (
    IMAGE_MODEL_SPECS,
    POINTS_PER_MILLION_TOKENS,
    resolve_dimensions,
    resolve_size_string,
    calculate_image_points,
    calculate_image_tokens,
    sanitize_gpt_image_raw,
    settle_gpt_image2_points,
    normalize_gpt_image_usage,
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


def test_gpt_image25_quality_axis_matches_20_anchors():
    """2.5 max 输出 token = 2.0 high;2.5 high = 2.0 medium。单价相同。"""
    w, h = 1024, 1024
    for model_25 in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
        assert calculate_image_tokens("max", w, h, model_25) == calculate_image_tokens("high", w, h, "gpt-image-2")
        assert calculate_image_tokens("high", w, h, model_25) == calculate_image_tokens("medium", w, h, "gpt-image-2")
        assert calculate_image_tokens("low", w, h, model_25) == calculate_image_tokens("low", w, h, "gpt-image-2")
        assert calculate_image_points("max", w, h, model_25) == calculate_image_points("high", w, h, "gpt-image-2")


def test_gpt_image25_quality_monotonic():
    toks = [
        calculate_image_tokens(q, 2048, 2048, "gpt-image-2.5-flare") for q in ("low", "medium", "high", "xhigh", "max")
    ]
    assert toks == sorted(toks)
    assert len(set(toks)) == 5


def test_family_specs_share_unit_price_and_pixel_table():
    gpt = IMAGE_MODEL_SPECS["gpt-image-2"]
    for name in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
        spec = IMAGE_MODEL_SPECS[name]
        assert spec["size_limits"] == gpt["size_limits"]
        assert spec["token_area_offset_pixels"] == gpt["token_area_offset_pixels"]
        assert spec["token_area_scale_denominator"] == gpt["token_area_scale_denominator"]
    assert POINTS_PER_MILLION_TOKENS == 21_000


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


def test_estimate_unknown_quality_falls_back_medium_for_that_model():
    assert estimate_gpt_image2_points("xhigh", "1:1", "1K") == estimate_gpt_image2_points("medium", "1:1", "1K")
    flare_xhigh = estimate_gpt_image2_points("xhigh", "1:1", "1K", model="gpt-image-2.5-flare")
    flare_med = estimate_gpt_image2_points("medium", "1:1", "1K", model="gpt-image-2.5-flare")
    assert flare_xhigh != flare_med
    assert flare_xhigh == calculate_image_points("xhigh", 1024, 1024, "gpt-image-2.5-flare")


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


@pytest.mark.parametrize("cls", [GptImage25SunburstDef, GptImage25FlareDef])
def test_gpt_image25_equivalent_quality_pairs(cls):
    """2.5 max=2.0 high;2.5 high=2.0 medium。同 label medium 不再同价。"""
    gpt = GptImage2Def()
    other = cls()
    req_20_high = _make_request(ratio="16:9", quality="high", image_size="2K")
    req_25_max = _make_request(ratio="16:9", quality="max", image_size="2K")
    req_20_med = _make_request(ratio="16:9", quality="medium", image_size="2K")
    req_25_high = _make_request(ratio="16:9", quality="high", image_size="2K")
    req_med = _make_request(ratio="16:9", quality="medium", image_size="2K")
    assert other.estimate_cost(req_25_max) == gpt.estimate_cost(req_20_high)
    assert other.estimate_cost(req_25_high) == gpt.estimate_cost(req_20_med)
    assert other.estimate_cost(req_med) != gpt.estimate_cost(req_med)


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
    flare = GptImage25FlareDef()
    for q in ("low", "medium", "high", "xhigh", "max"):
        req = _make_request(ratio="1:1", quality=q, image_size="2K")
        assert flare.estimate_cost(req) >= 1


_OFFICIAL_USAGE = {
    "created": 1713833628,
    "data": [{"b64_json": "..."}],
    "usage": {
        "total_tokens": 100,
        "input_tokens": 50,
        "output_tokens": 50,
        "input_tokens_details": {"text_tokens": 10, "image_tokens": 40},
    },
}

_GATEWAY_USAGE = {
    "taskId": "task_kfSpJuEXUteHLV0V3f87c4LOpvEzYsti",
    "status": "SUCCESS",
    "usage": {
        "taskId": "task_kfSpJuEXUteHLV0V3f87c4LOpvEzYsti",
        "model": None,
        "imageCount": 1,
        "rawUsage": {
            "cached_tokens": 0,
            "image_count": 1,
            "images": 1,
            "input_tokens": 1836,
            "input_tokens_details": {"image_tokens": 1508, "text_tokens": 328},
            "output_tokens": 7370,
            "output_tokens_details": {"image_tokens": 7370, "text_tokens": 0},
            "total_tokens": 9206,
        },
    },
}


def test_settle_official_usage_splits_input_output():
    """官方样例:50 out × $30 + 40 image in × $8 + 10 text in × $5。"""
    assert settle_gpt_image2_points(_OFFICIAL_USAGE) == 2
    wrapped = {"raw_task": _OFFICIAL_USAGE}
    assert settle_gpt_image2_points(wrapped) == 2


def test_settle_gateway_raw_usage():
    """网关 rawUsage:7370 out + 1508 image in + 328 text in。"""
    assert settle_gpt_image2_points(_GATEWAY_USAGE) == 165
    assert settle_gpt_image2_points({"raw_task": _GATEWAY_USAGE}) == 165


def test_settle_missing_usage_returns_none():
    assert settle_gpt_image2_points({}) is None
    assert settle_gpt_image2_points(None) is None


def test_family_settle_cost_matches_mapper():
    req = _make_request(ratio="1:1", quality="medium", image_size="2K")
    for cls in (GptImage2Def, GptImage25FlareDef, GptImage25SunburstDef):
        assert cls().settle_cost(req, _OFFICIAL_USAGE) == 2
        assert cls().settle_cost(req, _GATEWAY_USAGE) == 165
        assert cls().settle_cost(req, {}) is None


def test_settle_uses_normalized_usage_and_raw_task():
    """dispatcher:usage + raw_task 与官方/网关原文同价。"""
    official_u = normalize_gpt_image_usage(_OFFICIAL_USAGE)
    gateway_u = normalize_gpt_image_usage(_GATEWAY_USAGE)
    assert settle_gpt_image2_points(official_u) == 2
    assert settle_gpt_image2_points(gateway_u) == 165
    assert settle_gpt_image2_points({**official_u, "raw_task": _OFFICIAL_USAGE}) == 2
    assert settle_gpt_image2_points({**gateway_u, "raw_task": _GATEWAY_USAGE}) == 165
    aif_wrapped = {"result": _OFFICIAL_USAGE, "status": "已完成"}
    assert settle_gpt_image2_points(aif_wrapped) == 2


def test_settle_ignores_total_tokens_without_split():
    """禁止把 total_tokens 当 image output 整档 $30 计。"""
    assert settle_gpt_image2_points({"total_tokens": 9206}) is None
    assert settle_gpt_image2_points(_GATEWAY_USAGE) == 165


def test_settle_cached_tokens_prefer_image_input():
    uncached = {
        "output_tokens": 10_000,
        "input_tokens": 6_000,
        "input_tokens_details": {"image_tokens": 5_000, "text_tokens": 1_000},
    }
    cached = {
        **uncached,
        "cached_tokens": 5_000,
    }
    assert settle_gpt_image2_points(uncached) == 242
    assert settle_gpt_image2_points(cached) == 221


def test_sanitize_omits_b64_json():
    cleaned = sanitize_gpt_image_raw(_OFFICIAL_USAGE)
    data = cleaned["data"]
    assert isinstance(data, list)
    item = data[0]
    assert isinstance(item, dict)
    assert item["b64_json"] == "<omitted>"
    orig = _OFFICIAL_USAGE["data"][0]
    assert orig["b64_json"] == "..."
