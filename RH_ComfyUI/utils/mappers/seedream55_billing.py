"""Seedream 5.5 动态积分计价

计价规则(取自火山方舟官方「模型价格」页,doubao-seedream-5-5):
  - 输入图:0.05 元/张 = 5 积分/张(从首张起计,无免费额度)
  - 输出图单价(元/张,P 为单张输出图总像素):
      P ≤ 2.61M:0.13 元 = 13 积分
      2.61M < P ≤ 4.63M:0.23 元 = 23 积分

图层拆分场景:单次请求每个图层可能归属不同档位,按实际生成张数与
各自像素计费;请求按 layer_max_images 预扣,成功后按 usage 实扣
差额(settle)。未成功输出的图片不计费。

档位映射(估算用):1K / 1.5K 全部比例 ≤2.61M → 13 积分;
2K / auto / 显式像素按实际面积分档。
"""

from __future__ import annotations

from typing import Optional

# ── 计费常量 ──

# 输入图:0.05 元/张,从首张起计
INPUT_IMAGE_COST_POINTS: int = 5

# 输出图:P ≤ 2.61M → 13 积分,2.61M < P ≤ 4.63M → 23 积分
OUTPUT_COST_LOW_POINTS: int = 13
OUTPUT_COST_HIGH_POINTS: int = 23
OUTPUT_PIXEL_THRESHOLD_LOW: int = 2_610_000  # 2.61M(≈1536²×1.1025)
OUTPUT_PIXEL_THRESHOLD_HIGH: int = 4_630_000  # 4.63M(≈2048²×1.1025)

# 图层拆分默认最大输出图片数(官方默认 30,取值 [2, 100])
DEFAULT_MAX_IMAGES: int = 30
MIN_MAX_IMAGES: int = 2
MAX_MAX_IMAGES: int = 100

# 估算默认档位
_DEFAULT_TIER: str = "2K"

# 档位 → 单张输出积分(1K/1.5K 全部比例不超 2.61M;2K/auto 按高档估)
_TIER_POINTS: dict[str, int] = {
    "1K": OUTPUT_COST_LOW_POINTS,
    "1.5K": OUTPUT_COST_LOW_POINTS,
    "2K": OUTPUT_COST_HIGH_POINTS,
    "AUTO": OUTPUT_COST_HIGH_POINTS,
}


def _first_int(*values: object) -> Optional[int]:
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


def _pixels_from_size(size: object) -> Optional[int]:
    """'WxH' 字符串 → 总像素;解析不出返回 None。"""
    if not isinstance(size, str):
        return None
    parts = size.strip().lower().split("x")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    return int(parts[0]) * int(parts[1])


def _points_for_pixels(pixels: int) -> int:
    if pixels <= OUTPUT_PIXEL_THRESHOLD_LOW:
        return OUTPUT_COST_LOW_POINTS
    return OUTPUT_COST_HIGH_POINTS


def _points_for_size_str(size: object) -> Optional[int]:
    """按实际 'WxH' 计单张输出积分(结算用);解析不出返回 None。"""
    pixels = _pixels_from_size(size)
    if pixels is None or pixels <= 0:
        return None
    return _points_for_pixels(pixels)


def _tier_output_points(size: object) -> int:
    """估算用:档位名 / 显式 'WxH' → 单张输出积分;未知按高档。"""
    if isinstance(size, str):
        s = size.strip()
        pts = _points_for_size_str(s)
        if pts is not None:
            return pts
        return _TIER_POINTS.get(s.upper(), OUTPUT_COST_HIGH_POINTS)
    return _TIER_POINTS[_DEFAULT_TIER]


def estimate_seedream55_points(
    num_input_images: int = 0,
    size: object = None,
    *,
    layer_decomposition: bool = False,
    layer_max_images: int = DEFAULT_MAX_IMAGES,
) -> int:
    """预扣估算:输入图×5 + 输出图(1 张或 layer_max_images 张)× 档位价。

    Args:
        num_input_images: 参考图数量(len(request.images))
        size: image_size 档位("1K"/"1.5K"/"2K"/"auto")或显式 "WxH"
        layer_decomposition: 图层拆分模式
        layer_max_images: 拆分模式最大输出图片数(含底图,默认 30)
    """
    n_in = max(int(num_input_images or 0), 0)
    per = _tier_output_points(size)
    if layer_decomposition:
        n_out = _first_int(layer_max_images) or DEFAULT_MAX_IMAGES
        n_out = max(n_out, MIN_MAX_IMAGES)
        return max(n_in * INPUT_IMAGE_COST_POINTS + n_out * per, 1)
    return max(n_in * INPUT_IMAGE_COST_POINTS + per, 1)


def settle_seedream55_points(usage: object) -> Optional[int]:
    """上游 usage 实扣:输入图×5 + Σ 每张输出图按实际像素分档。

    usage 为 dispatch 拼接的 {mapper usage + raw_task=厂商原始响应}。
    data[].size 缺失/解析不出时返回 None(维持预扣)。
    """
    if not isinstance(usage, dict):
        return None
    raw_task = usage["raw_task"] if "raw_task" in usage else None
    if not isinstance(raw_task, dict):
        return None
    data = raw_task["data"] if "data" in raw_task else None
    if not isinstance(data, list) or not data:
        return None

    out_points = 0
    for item in data:
        if not isinstance(item, dict):
            return None
        pts = _points_for_size_str(item["size"] if "size" in item else None)
        if pts is None:
            return None
        out_points += pts

    n_in = _first_int(usage["input_images"] if "input_images" in usage else None)
    if n_in is None:
        u = raw_task["usage"] if "usage" in raw_task else None
        n_in = _first_int(u["input_images"] if isinstance(u, dict) and "input_images" in u else None)
    return max((n_in or 0) * INPUT_IMAGE_COST_POINTS + out_points, 1)


__all__ = [
    "DEFAULT_MAX_IMAGES",
    "INPUT_IMAGE_COST_POINTS",
    "MAX_MAX_IMAGES",
    "MIN_MAX_IMAGES",
    "OUTPUT_COST_HIGH_POINTS",
    "OUTPUT_COST_LOW_POINTS",
    "OUTPUT_PIXEL_THRESHOLD_HIGH",
    "OUTPUT_PIXEL_THRESHOLD_LOW",
    "estimate_seedream55_points",
    "settle_seedream55_points",
]
