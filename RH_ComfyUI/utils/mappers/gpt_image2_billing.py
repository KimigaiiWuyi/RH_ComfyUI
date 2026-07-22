"""GPT-Image-2 动态积分计价

计价规则:210 元(RMB)/1M tokens,1 积分 = 1 分钱 → 210 元 = 21_000 积分 / 1M tokens。
token 计算公式由上游 OpenAI 兼容网关公开,按 quality 档位 + 输出像素面积分档。

size 像素值由 ratio + image_size 二维映射决定(与 openai_image/api.py 的
_RATIO_SIZE_MAP 保持一致);ratio="auto" 时按 1024x1024 估算。
"""

from __future__ import annotations

from typing import Optional

# ── 模型 spec(取自网关公开文档) ──

IMAGE_MODEL_SPECS: dict[str, dict] = {
    "gpt-image-2": {
        "size_limits": {
            "step_px": 16,
            "min_pixels": 655_360,
            "max_pixels": 8_294_400,
            "max_dimension_px": 3_840,
            "max_aspect_ratio": 3.0,
        },
        "quality_axis_factors": {"low": 16, "medium": 48, "high": 96},
        "token_area_offset_pixels": 2_000_000,
        "token_area_scale_denominator": 4_000_000,
    },
}

# ── 计费常量 ──

# 210 元 / 1M tokens,1 元 = 100 积分 → 210 * 100 = 21_000 积分 / 1M tokens
POINTS_PER_MILLION_TOKENS: int = 21_000

# ratio + image_size → 像素尺寸(与 openai_image/api.py::_RATIO_SIZE_MAP 保持一致)
#
# 每个 cell 都必须满足 OpenAI 上游的 4 条硬约束(参见 IMAGE_MODEL_SPECS.size_limits):
#   1. 两边都是 16 的倍数
#   2. max edge ≤ 3840px
#   3. 长宽比 = ratio(精确),且 ≤ 3:1
#   4. 像素 ∈ [655_360, 8_294_400]
# 此外:总像素 > 3_686_400 (即 2560x1440) 视为 2K+ 实验性输出。
# 本表以 16:9 2K = 2560x1440 为基准,其余 2K 行同比例上抬;4K 行贴近硬上限,
# tier 语义:短边 ≈ 1K/2K/4K。
_RATIO_SIZE_MAP: dict[str, dict[str, str]] = {
    # Landscape (width > height)
    "1:1": {"1K": "1024x1024", "2K": "2560x2560", "4K": "2880x2880"},
    "16:9": {"1K": "1792x1008", "2K": "2560x1440", "4K": "3840x2160"},
    "4:3": {"1K": "1280x960", "2K": "2560x1920", "4K": "3264x2448"},
    "3:2": {"1K": "1536x1024", "2K": "3072x2048", "4K": "3504x2336"},
    "2:1": {"1K": "1152x576", "2K": "2560x1280", "4K": "3840x1920"},
    "21:9": {"1K": "2464x1056", "2K": "2800x1200", "4K": "3808x1632"},
    # Portrait (width < height)
    "9:16": {"1K": "1008x1792", "2K": "1440x2560", "4K": "2160x3840"},
    "3:4": {"1K": "960x1280", "2K": "1920x2560", "4K": "2448x3264"},
    "2:3": {"1K": "1024x1536", "2K": "2048x3072", "4K": "2336x3504"},
    "1:2": {"1K": "576x1152", "2K": "1280x2560", "4K": "1920x3840"},
}

# ratio="auto" 时的默认估算尺寸
_AUTO_WIDTH: int = 1024
_AUTO_HEIGHT: int = 1024


def _parse_size(size_str: str) -> tuple[int, int]:
    """'1024x1024' → (1024, 1024)"""
    w, _, h = size_str.lower().partition("x")
    return int(w), int(h)


def resolve_dimensions(ratio: Optional[str], image_size: Optional[str]) -> tuple[int, int]:
    """ratio + image_size → (width, height) 像素。

    ratio 为 'auto' / None / 未匹配 → 回落 _AUTO_WIDTH x _AUTO_HEIGHT。
    """
    if not ratio or ratio == "auto":
        return _AUTO_WIDTH, _AUTO_HEIGHT
    tier = image_size if image_size in ("1K", "2K", "4K") else "2K"
    tier_map = _RATIO_SIZE_MAP.get(ratio)
    if tier_map is None:
        return _AUTO_WIDTH, _AUTO_HEIGHT
    size_str = tier_map.get(tier, "2048x2048")
    return _parse_size(size_str)


def calculate_image_tokens(
    quality: str,
    width: int,
    height: int,
    model: str = "gpt-image-2",
) -> int:
    """计算生成一张图消耗的 tokens(纯函数,无 IO)。

    公式(上游公开):
        long_edge  = max(width, height)
        short_edge = min(width, height)
        short_axis_factor =
            (2 * quality_axis_factor * short_edge + long_edge) // (2 * long_edge)
        tokens =
            (quality_axis_factor
             * short_axis_factor
             * (token_area_offset_pixels + width * height)
             + token_area_scale_denominator - 1)
            // token_area_scale_denominator
    """
    spec = IMAGE_MODEL_SPECS[model]
    quality_axis_factor = spec["quality_axis_factors"][quality]
    long_edge = max(width, height)
    short_edge = min(width, height)
    short_axis_factor = (2 * quality_axis_factor * short_edge + long_edge) // (2 * long_edge)

    return (
        quality_axis_factor * short_axis_factor * (spec["token_area_offset_pixels"] + width * height)
        + spec["token_area_scale_denominator"]
        - 1
    ) // spec["token_area_scale_denominator"]


def calculate_image_points(
    quality: str,
    width: int,
    height: int,
    model: str = "gpt-image-2",
) -> int:
    """计算生成一张图消耗的积分(分),向上取整到积分。

    = tokens / 1_000_000 * POINTS_PER_MILLION_TOKENS,最小 1 积分。
    """
    tokens = calculate_image_tokens(quality, width, height, model)
    # tokens * 21_000 / 1_000_000 = tokens * 21 / 1000 (积分)
    points = (tokens * POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def estimate_gpt_image2_points(
    quality: Optional[str],
    ratio: Optional[str],
    image_size: Optional[str],
) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    quality 缺失 → 'medium';ratio 缺失 / 'auto' → 1024x1024。
    """
    q = quality if quality in ("low", "medium", "high") else "medium"
    w, h = resolve_dimensions(ratio, image_size)
    return calculate_image_points(q, w, h)


__all__ = [
    "IMAGE_MODEL_SPECS",
    "POINTS_PER_MILLION_TOKENS",
    "resolve_dimensions",
    "calculate_image_tokens",
    "calculate_image_points",
    "estimate_gpt_image2_points",
]
