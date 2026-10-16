"""Nano Banana 2 Lite 动态积分计价

官方(gemini-3.1-flash-lite-image,Standard)图片输出 30 美元/1M tokens。
1 美元 = 100 积分 → 30 * 100 = 3_000 积分 / 1M tokens。
价格页(2026-10):

  - 1K (1024px) : 1120 tokens → $0.0336 → 4 积分

没有 2K / 4K。输入(文本/图片) $0.25/1M;文本与思考输出 $1.50/1M。
预扣只按 1K 出图。成功后按 usage_metadata 补入输入、文本和思考 token。
"""

from __future__ import annotations

from typing import Optional

# 30 美元 / 1M tokens,1 美元 = 100 积分 → 3_000 积分 / 1M tokens
POINTS_PER_MILLION_TOKENS: int = 3_000
# 标准档:输入 $0.25/1M → 25 积分/1M;文本与思考 $1.50/1M → 150 积分/1M
INPUT_POINTS_PER_MILLION: int = 25
TEXT_POINTS_PER_MILLION: int = 150

OUTPUT_TOKENS_BY_SIZE: dict[str, int] = {
    "1K": 1120,  # 1K (1024x1024px) → $0.0336
}

_DEFAULT_SIZE: str = "1K"


def calculate_output_points(image_size: Optional[str]) -> int:
    """按输出分辨率计算积分,向上取整。非 1K 回落到 1K。"""
    size = image_size if image_size in OUTPUT_TOKENS_BY_SIZE else _DEFAULT_SIZE
    tokens = OUTPUT_TOKENS_BY_SIZE[size]
    points = (tokens * POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def estimate_nanobanana2_lite_points(image_size: Optional[str]) -> int:
    """供 estimate_cost。缺失或 2K/4K 都按唯一的 1K 档。"""
    return calculate_output_points(image_size)


def settle_nanobanana2_lite_points(usage: object) -> Optional[int]:
    """厂商 usage 实扣。解析不到时 None,维持预扣。"""
    from .gemini_image_usage import settle_gemini_image_points

    return settle_gemini_image_points(
        usage,
        input_points_per_million=INPUT_POINTS_PER_MILLION,
        text_points_per_million=TEXT_POINTS_PER_MILLION,
        image_points_per_million=POINTS_PER_MILLION_TOKENS,
    )


__all__ = [
    "POINTS_PER_MILLION_TOKENS",
    "INPUT_POINTS_PER_MILLION",
    "TEXT_POINTS_PER_MILLION",
    "OUTPUT_TOKENS_BY_SIZE",
    "calculate_output_points",
    "estimate_nanobanana2_lite_points",
    "settle_nanobanana2_lite_points",
]
