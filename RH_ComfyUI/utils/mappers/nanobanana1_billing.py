"""Nano Banana 1 动态积分计价

计价规则:30 美元/1M tokens,1 美元 = 100 积分 → 30 * 100 = 3_000 积分 / 1M tokens。
一代模型无尺寸档位,输出分辨率最高 1024x1024px,固定消耗 1290 tokens:

  - 1K (1024x1024px) : 1290 tokens → $0.039 → 4 积分

point_cost 仅作兜底,实际计费以本模块为准。
"""

from __future__ import annotations

# ── 计费常量 ──

# 30 美元 / 1M tokens,1 美元 = 100 积分 → 30 * 100 = 3_000 积分 / 1M tokens
POINTS_PER_MILLION_TOKENS: int = 3_000

# 一代模型固定 token 消耗(最高 1024x1024px)
OUTPUT_TOKENS: int = 1290


def calculate_output_points() -> int:
    """计算单张输出图片的积分(分),向上取整。

    一代模型无尺寸档位,固定 1290 tokens。
    """
    points = (OUTPUT_TOKENS * POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def estimate_nanobanana1_points() -> int:
    """估算积分(供 estimate_cost 调用)。

    一代模型无尺寸档位,返回固定值。
    """
    return calculate_output_points()


__all__ = [
    "POINTS_PER_MILLION_TOKENS",
    "OUTPUT_TOKENS",
    "calculate_output_points",
    "estimate_nanobanana1_points",
]
