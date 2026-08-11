"""Nano Banana Pro 动态积分计价(独立于 GPT-Image-2)

计价规则(取自官方文档):
  - 图片输入:每张图片 0.0011 美元(≈ 0.11 积分)
  - 图片输出:120 美元/1M tokens,1 美元 = 100 积分 → 120 * 100 = 12_000 积分 / 1M tokens
    - 1K ~ 2K (1048x1048 ~ 2048x2048px): 1120 tokens → $0.134 → 14 积分
    - 4K (4096x4096px)                  : 2000 tokens → $0.24  → 24 积分

注:输入 token 单价远低于输出,故输入按固定 USD 价折算;输出按分辨率分档。
point_cost 仅作 image_size 参数缺失时的兜底(按 1K-2K 档估算)。
"""

from __future__ import annotations

import math
from typing import Optional

# ── 计费常量 ──

# 输出:120 美元 / 1M tokens,1 美元 = 100 积分 → 120 * 100 = 12_000 积分 / 1M tokens
OUTPUT_POINTS_PER_MILLION_TOKENS: int = 12_000

# 输入:每张图片 0.0011 美元 = 0.11 积分(输入/输出单价不同,不共用同一个汇率)
INPUT_COST_PER_IMAGE_POINTS: float = 0.0011 * 100  # 0.11 积分/张

# ── 分辨率分档 → 消耗 tokens(取自官方文档) ──

OUTPUT_TOKENS_BY_SIZE: dict[str, int] = {
    "1K": 1120,  # 1024x1024px
    "2K": 1120,  # 2048x2048px(同 1K 档 token 数)
    "4K": 2000,  # 4096x4096px
}

# image_size 参数缺失时的默认档位
_DEFAULT_SIZE: str = "2K"


def _output_points(image_size: Optional[str]) -> int:
    """按输出分辨率计算输出积分(分),向上取整。"""
    size = image_size if image_size in OUTPUT_TOKENS_BY_SIZE else _DEFAULT_SIZE
    tokens = OUTPUT_TOKENS_BY_SIZE[size]
    points = (tokens * OUTPUT_POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def _input_points(num_input_images: int) -> int:
    """计算输入图片积分(分),向上取整。

    无输入图片返回 0;有输入图片时按张数×单价后向上取整,最小 1 积分。
    """
    if num_input_images <= 0:
        return 0
    return max(math.ceil(num_input_images * INPUT_COST_PER_IMAGE_POINTS), 1)


def calculate_banana_pro_points(
    num_input_images: int,
    image_size: Optional[str],
) -> int:
    """计算总积分 = 输入积分 + 输出积分,最小 1 积分。"""
    total = _input_points(num_input_images) + _output_points(image_size)
    return max(total, 1)


def estimate_banana_pro_points(
    num_input_images: int = 0,
    image_size: Optional[str] = None,
) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    Args:
        num_input_images: 输入图片数量(从 request.images 长度取得)
        image_size: 输出分辨率档位(1K/2K/4K),缺失按 2K 估算
    """
    return calculate_banana_pro_points(num_input_images, image_size)


__all__ = [
    "OUTPUT_POINTS_PER_MILLION_TOKENS",
    "INPUT_COST_PER_IMAGE_POINTS",
    "OUTPUT_TOKENS_BY_SIZE",
    "calculate_banana_pro_points",
    "estimate_banana_pro_points",
]
