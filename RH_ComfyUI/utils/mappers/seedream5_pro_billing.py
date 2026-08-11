"""Seedream 5.0 Pro 动态积分计价

计价规则(取自官方文档):
  - 输入图:首张免费,第 2 张起每张 0.02 元 = 2 积分/张
  - 输出图单价(元/张):
      ≤ 236 万像素:0.30 元 = 30 积分
      > 236 万像素:0.60 元 = 60 积分

输出像素由 size_mode 档位决定(Pro 仅 1K/2K 两档):
  - 1K (1024x1024 = 1_048_576 像素) → ≤ 236 万 → 30 积分
  - 2K (2048x2048 = 4_194_304 像素) → > 236 万 → 60 积分

point_cost 仅作兜底。
"""

from __future__ import annotations

from typing import Optional

# ── 计费常量 ──

# 输入图:首张免费,第 2 张起每张 0.02 元 = 2 积分
INPUT_IMAGE_COST_POINTS: int = 2

# 输出图:≤ 236 万像素 0.30 元 = 30 积分,> 236 万像素 0.60 元 = 60 积分
OUTPUT_COST_LOW_POINTS: int = 30  # ≤ 236 万像素
OUTPUT_COST_HIGH_POINTS: int = 60  # > 236 万像素
OUTPUT_PIXEL_THRESHOLD: int = 2_360_000  # 236 万像素分界线

# Pro 仅支持 1K/2K 两档,像素数由档位决定
_SIZE_MODE_PIXELS: dict[str, int] = {
    "1K": 1024 * 1024,  # 1_048_576 像素
    "2K": 2048 * 2048,  # 4_194_304 像素
}

# image_size 参数缺失时的默认档位
_DEFAULT_SIZE_MODE: str = "2K"


def _input_image_points(num_input_images: int) -> int:
    """计算输入图片积分。首张免费,第 2 张起每张 2 积分。"""
    if num_input_images <= 1:
        return 0
    return (num_input_images - 1) * INPUT_IMAGE_COST_POINTS


def _output_image_points(size_mode: Optional[str]) -> int:
    """按输出分辨率档位计算输出积分。"""
    size = size_mode if size_mode in _SIZE_MODE_PIXELS else _DEFAULT_SIZE_MODE
    pixels = _SIZE_MODE_PIXELS[size]
    if pixels <= OUTPUT_PIXEL_THRESHOLD:
        return OUTPUT_COST_LOW_POINTS
    return OUTPUT_COST_HIGH_POINTS


def calculate_seedream5_pro_points(
    num_input_images: int,
    size_mode: Optional[str],
) -> int:
    """计算总积分 = 输入图片积分 + 输出图片积分,最小 1 积分。"""
    total = _input_image_points(num_input_images) + _output_image_points(size_mode)
    return max(total, 1)


def estimate_seedream5_pro_points(
    num_input_images: int = 0,
    size_mode: Optional[str] = None,
) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    Args:
        num_input_images: 输入图片数量(从 request.images 长度取得)
        size_mode: 输出分辨率档位(1K/2K),缺失按 2K 估算
    """
    return calculate_seedream5_pro_points(num_input_images, size_mode)


__all__ = [
    "INPUT_IMAGE_COST_POINTS",
    "OUTPUT_COST_LOW_POINTS",
    "OUTPUT_COST_HIGH_POINTS",
    "OUTPUT_PIXEL_THRESHOLD",
    "calculate_seedream5_pro_points",
    "estimate_seedream5_pro_points",
]
