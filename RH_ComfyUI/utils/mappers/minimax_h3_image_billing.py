"""MiniMax H3-Image 动态积分计价(官方 MiniMax 与 fal 同源模型,统一模型级曲线)

官方按 token 计费(标准版,人民币元 / 100 万 tokens):
  - 文本输入(含 Prompt 增强): 15 元/M
  - 图片输入(编辑参考图):     10 元/M
  - 图片输出:                 25 元/M
Highspeed 变体:文本输入 15、图片输入 20、图片输出 50 元/M。

输出 token 与像素面积近似线性:实测 1K/2K 档 ≈ 4080、4K ≈ 16320,
按「每 4,194,304 像素一档、每档 4080 token」折算。

1 元 = 100 积分;向上取整,最小 1。estimate 为预扣估算,
settle 用上游 usage 实报 token 精确对齐(只做差额)。
"""

from __future__ import annotations

import math
from typing import Any, Optional

_YUAN_TO_POINTS: int = 100

# 变体 → (文本输入, 图片输入, 图片输出) 元 / 1M tokens
_RATES_YUAN_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "standard": (15.0, 10.0, 25.0),
    "highspeed": (15.0, 20.0, 50.0),
}
_MODEL_VARIANTS: tuple[str, ...] = tuple(_RATES_YUAN_PER_MTOK)

# 估算用常量:输入文本按 4000 token(含 Prompt 增强膨胀)、输入图按 3000 token/张
_TEXT_TOKENS_EST = 4000
_INPUT_IMAGE_TOKENS_EST = 3000

# 输出 token ≈ 面积 / 4,194,304 px × 4080;1K(≈1M px)与 2K(≈4.2M px)同档
_PIXELS_PER_BLOCK = 4_194_304
_TOKENS_PER_BLOCK = 4080
_TIER_OUTPUT_TOKENS: dict[str, int] = {
    "1K": _TOKENS_PER_BLOCK,
    "2K": _TOKENS_PER_BLOCK,
    "4K": 4 * _TOKENS_PER_BLOCK,
}
_DEFAULT_TIER = "2K"


def _norm_variant(raw: object) -> str:
    v = str(raw or "").strip().lower()
    return "highspeed" if v == "highspeed" else "standard"


def _norm_tier(raw: object) -> str:
    t = str(raw or "").strip().upper()
    return t if t in _TIER_OUTPUT_TOKENS else _DEFAULT_TIER


def _output_tokens(image_size: object, width: object = None, height: object = None) -> int:
    """档位 → token;显式像素按面积块折算(与官方 token 曲线一致)。"""
    try:
        w = int(str(width)) if width is not None else 0
        h = int(str(height)) if height is not None else 0
    except (TypeError, ValueError):
        w = h = 0
    if w > 0 and h > 0:
        return _TOKENS_PER_BLOCK * max(1, math.ceil((w * h) / _PIXELS_PER_BLOCK))
    return _TIER_OUTPUT_TOKENS[_norm_tier(image_size)]


def _yuan_to_points(yuan: float) -> int:
    return max(math.ceil(yuan * _YUAN_TO_POINTS), 1)


def estimate_minimax_h3_image_points(
    image_size: object = None,
    num_input_images: int = 0,
    *,
    variant: object = "standard",
    width: object = None,
    height: object = None,
) -> int:
    """纯函数预扣估算:文本 4000 + 输入图 3000×N + 输出 token 分档。"""
    text_rate, image_in_rate, image_out_rate = _RATES_YUAN_PER_MTOK[_norm_variant(variant)]
    n_img = max(int(num_input_images or 0), 0)
    out_tokens = _output_tokens(image_size, width, height)
    yuan = (
        _TEXT_TOKENS_EST * text_rate + n_img * _INPUT_IMAGE_TOKENS_EST * image_in_rate + out_tokens * image_out_rate
    ) / 1_000_000
    return _yuan_to_points(yuan)


def _first_int(*values: Any) -> Optional[int]:
    for v in values:
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v if v >= 0 else None
        if isinstance(v, float):
            return int(v) if v >= 0 else None
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def _sub_ints(detail: Any, *keys: str) -> Optional[int]:
    if not isinstance(detail, dict):
        return None
    for key in keys:
        got = _first_int(detail[key] if key in detail else None)
        if got is not None:
            return got
    return None


def settle_minimax_h3_image_points(usage: Any) -> Optional[int]:
    """上游 usage 实扣;解析不出 token 用量时返回 None(维持预扣)。

    兼容两种回包形态:
    - 官方:{input_tokens_details:{text,image}, output_tokens_details:{image}}
    - fal  / 扁平:{text_tokens, image_input_tokens, image_output_tokens}
    """
    if not isinstance(usage, dict):
        return None
    vendor = usage["vendor_usage"] if "vendor_usage" in usage else usage
    if not isinstance(vendor, dict):
        return None

    in_detail = vendor["input_tokens_details"] if "input_tokens_details" in vendor else None
    out_detail = vendor["output_tokens_details"] if "output_tokens_details" in vendor else None

    text = _sub_ints(in_detail, "text_tokens", "input_text_tokens")
    if text is None:
        text = _first_int(vendor.get("text_tokens"), vendor.get("input_text_tokens"))
    image_in = _sub_ints(in_detail, "image_tokens", "image_input_tokens")
    if image_in is None:
        image_in = _first_int(vendor.get("image_input_tokens"), vendor.get("input_image_tokens"))
    image_out = _sub_ints(out_detail, "image_tokens", "image_output_tokens")
    if image_out is None:
        image_out = _first_int(vendor.get("image_output_tokens"), vendor.get("output_image_tokens"))

    if text is None and image_in is None and image_out is None:
        return None

    variant = _norm_variant(usage.get("model"))
    text_rate, image_in_rate, image_out_rate = _RATES_YUAN_PER_MTOK[variant]
    yuan = ((text or 0) * text_rate + (image_in or 0) * image_in_rate + (image_out or 0) * image_out_rate) / 1_000_000
    return _yuan_to_points(yuan)


__all__ = [
    "MODEL_VARIANTS",
    "estimate_minimax_h3_image_points",
    "settle_minimax_h3_image_points",
]

MODEL_VARIANTS = _MODEL_VARIANTS
