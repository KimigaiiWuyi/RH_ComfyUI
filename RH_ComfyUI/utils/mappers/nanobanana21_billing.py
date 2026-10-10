"""Nano Banana 2.1 动态积分计价

官方(gemini-nano-banana-2.1,Standard)图片输出 30 美元/1M tokens。
1 美元 = 100 积分 → 30 * 100 = 3_000 积分 / 1M tokens。
token 与单价取自 Gemini API 价格页(2026-10):

  - 1K (1024px) : 1120 tokens → $0.0336 →  4 积分
  - 2K (2048px) : 1680 tokens → $0.0504 →  6 积分
  - 4K (4096px) : 3780 tokens → $0.113  → 12 积分

与 Nano Banana 2(60 美元/1M,含 512 档)不是同一条曲线。
2.1 没有 512 档。预扣只按输出图片。成功后按 usage_metadata 补入输入、文本和思考 token。
point_cost 仅作 image_size 缺失时的静态兜底(按默认 1K 档)。
"""

from __future__ import annotations

from typing import Optional

# 30 美元 / 1M tokens,1 美元 = 100 积分 → 30 * 100 = 3_000 积分 / 1M tokens
POINTS_PER_MILLION_TOKENS: int = 3_000
# 标准档:输入(文本/图片)$1.50/1M;文本与思考输出 $7.50/1M。图片输出沿用上面的 3_000。
INPUT_POINTS_PER_MILLION: int = 150
TEXT_POINTS_PER_MILLION: int = 750

# 价格页:4K 为 3780 tokens,不是 2 代那张表的 2520。
OUTPUT_TOKENS_BY_SIZE: dict[str, int] = {
    "1K": 1120,  # 1K (1024x1024px) → $0.0336
    "2K": 1680,  # 2K (2048x2048px) → $0.0504
    "4K": 3780,  # 4K (4096x4096px) → $0.113
}

# image_size 参数缺失时的默认档位(官方默认 1K)
_DEFAULT_SIZE: str = "1K"


def calculate_output_points(image_size: Optional[str]) -> int:
    """按输出分辨率计算积分(分),向上取整。

    image_size 未匹配任何档位时回落到 _DEFAULT_SIZE。
    """
    size = image_size if image_size in OUTPUT_TOKENS_BY_SIZE else _DEFAULT_SIZE
    tokens = OUTPUT_TOKENS_BY_SIZE[size]
    # 向上取整到积分:tokens * 3000 / 1_000_000
    points = (tokens * POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def estimate_nanobanana21_points(image_size: Optional[str]) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    image_size 缺失 → 按 1K 档(官方默认)估算。
    """
    return calculate_output_points(image_size)


def settle_nanobanana21_points(usage: object) -> Optional[int]:
    """厂商 usage_metadata 实扣。解析不到时 None,维持预扣。"""
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
    "estimate_nanobanana21_points",
    "settle_nanobanana21_points",
]
