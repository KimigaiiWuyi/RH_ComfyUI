"""Nano Banana 2 动态积分计价

计价规则:60 美元/1M tokens,1 美元 = 100 积分 → 60 * 100 = 6_000 积分 / 1M tokens。
按输出图片分辨率分档计费,token 消耗取自官方文档:

  - 0.5K (512px) :  747 tokens → $0.045 →  5 积分
  - 1K (1024px)   : 1120 tokens → $0.067 →  7 积分
  - 2K (2048px)   : 1680 tokens → $0.101 → 11 积分
  - 4K (4096px)   : 2520 tokens → $0.151 → 16 积分

与 Nano Banana 2.1 不是同一条曲线。point_cost 仅作 image_size 缺失时的兜底(按 2K 档)。
"""

from __future__ import annotations

from typing import Optional

# ── 计费常量 ──

# 60 美元 / 1M tokens,1 美元 = 100 积分 → 60 * 100 = 6_000 积分 / 1M tokens
POINTS_PER_MILLION_TOKENS: int = 6_000
# 标准档:输入(文本/图片)$0.50/1M;文本与思考输出 $3/1M。图片输出沿用上面的 6_000。
INPUT_POINTS_PER_MILLION: int = 50
TEXT_POINTS_PER_MILLION: int = 300

# ── 分辨率分档 → 消耗 tokens(取自官方文档) ──

OUTPUT_TOKENS_BY_SIZE: dict[str, int] = {
    "512": 747,  # 0.5K (512px)
    "1K": 1120,  # 1K (1024x1024px)
    "2K": 1680,  # 2K (2048x2048px)
    "4K": 2520,  # 4K (4096x4096px)
}

# image_size 参数缺失时的默认档位
_DEFAULT_SIZE: str = "2K"


def calculate_output_points(image_size: Optional[str]) -> int:
    """按输出分辨率计算积分(分),向上取整。

    image_size 未匹配任何档位时回落到 _DEFAULT_SIZE。
    """
    size = image_size if image_size in OUTPUT_TOKENS_BY_SIZE else _DEFAULT_SIZE
    tokens = OUTPUT_TOKENS_BY_SIZE[size]
    # 向上取整到积分:tokens * 6000 / 1_000_000
    points = (tokens * POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def estimate_nanobanana2_points(image_size: Optional[str]) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    image_size 缺失 → 按 2K 档(默认值)估算。
    """
    return calculate_output_points(image_size)


def settle_nanobanana2_points(usage: object) -> Optional[int]:
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
    "estimate_nanobanana2_points",
    "settle_nanobanana2_points",
]
