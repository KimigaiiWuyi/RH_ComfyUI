"""可灵积分。

官方价是资源包「单位」,视频约 ¥1/单位,图片不是。
插件积分按 $1 = 100,这里取 $1 ≈ ¥7.2,即 1 元 = 14 积分,向上取整,最少 1。

视频单价(元/秒):
- 3.0 Turbo 只有有声:720p 0.8 / 1080p 1.0
- 3.0 无参考无声 0.6/0.8/3.0,有声 0.9/1.2/3.0(720/1080/4k)
- 3.0 Omni 无参考无声 0.6/0.8/3.0,无参考有声 0.8/1.0/3.0
- 有参考视频按无声参考价 0.9/1.2,4k 仍是 3.0(有声参考没有单独一行)
- O1 无参考 0.6/0.8,有参考 0.9/1.2
- 2.6 无声 0.3/0.5;1080p 有声 1.0;1080p 指定音色 1.2
- 2.5 Turbo 无声 0.3/0.5

图片(元/张,主体补全按次):
- Image 3.0 / Omni 1K、2K = 0.2;Omni 4K = 0.4
- Image O1 1K、2K = 0.2
- Image 2.1 文生 0.1 / 单图 0.2 / 多图 0.4
- 通用扩图 0.2/张;主体补全 0.5/次
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

POINTS_PER_CNY = Decimal("14")

_SILENT_30 = {"720p": Decimal("0.6"), "1080p": Decimal("0.8"), "4k": Decimal("3.0")}
_AUDIO_30 = {"720p": Decimal("0.9"), "1080p": Decimal("1.2"), "4k": Decimal("3.0")}
_OMNI_AUDIO = {"720p": Decimal("0.8"), "1080p": Decimal("1.0"), "4k": Decimal("3.0")}
_REF_VIDEO = {"720p": Decimal("0.9"), "1080p": Decimal("1.2"), "4k": Decimal("3.0")}
_O1_PLAIN = {"720p": Decimal("0.6"), "1080p": Decimal("0.8")}
_O1_REF = {"720p": Decimal("0.9"), "1080p": Decimal("1.2")}
_SILENT_26 = {"720p": Decimal("0.3"), "1080p": Decimal("0.5")}
_TURBO = {"720p": Decimal("0.8"), "1080p": Decimal("1.0")}


def cny_to_points(yuan: Decimal) -> int:
    """人民币向上取整成积分,最少 1。"""
    points = int((yuan * POINTS_PER_CNY).to_integral_value(rounding=ROUND_CEILING))
    return max(1, points)


def _rate(table: dict[str, Decimal], resolution: str) -> Decimal:
    if resolution not in table:
        raise ValueError(f"未知可灵分辨率 {resolution}")
    return table[resolution]


def video_yuan(
    model: str,
    *,
    resolution: str,
    seconds: int,
    sound: str,
    ref_video: bool,
    omni: bool = False,
) -> Decimal:
    """``sound`` 取 always / native / silent / voice。

    3.0 标准有声和 Omni 无参考有声不是同一行单价。
    """
    if seconds < 1:
        raise ValueError("时长至少 1 秒")
    if model == "kling-3.0-turbo":
        rate = _rate(_TURBO, resolution)
    elif model == "kling-3.0":
        if ref_video:
            rate = _rate(_REF_VIDEO, resolution)
        elif sound == "native" and omni:
            rate = _rate(_OMNI_AUDIO, resolution)
        elif sound == "native":
            rate = _rate(_AUDIO_30, resolution)
        else:
            rate = _rate(_SILENT_30, resolution)
    elif model == "kling-o1":
        rate = _rate(_O1_REF if ref_video else _O1_PLAIN, resolution)
    elif model == "kling-2.6":
        if sound == "voice":
            rate = Decimal("1.2")
        elif sound == "native":
            rate = Decimal("1.0")
        else:
            rate = _rate(_SILENT_26, resolution)
    elif model == "kling-2.5-turbo":
        rate = _rate(_SILENT_26, resolution)
    else:
        raise ValueError(f"未知可灵视频模型 {model}")
    return rate * Decimal(seconds)


def image_yuan(
    model: str,
    *,
    tier: str,
    resolution: str,
    count: int,
) -> Decimal:
    """``tier`` 取 t2i / i2i / multi / omni / outpaint / subject。subject 忽略张数。"""
    if model == "kling-image-common" and tier == "subject":
        return Decimal("0.5")
    if count < 1:
        raise ValueError("出图张数至少 1")
    if tier == "outpaint":
        each = Decimal("0.2")
    elif model == "kling-image-2.1":
        if tier == "multi":
            each = Decimal("0.4")
        elif tier == "i2i":
            each = Decimal("0.2")
        else:
            each = Decimal("0.1")
    elif resolution == "4k":
        each = Decimal("0.4")
    else:
        each = Decimal("0.2")
    return each * Decimal(count)


def video_point_bounds(model: str) -> tuple[int, int]:
    if model == "kling-3.0-turbo":
        low = video_yuan(model, resolution="720p", seconds=3, sound="always", ref_video=False)
        high = video_yuan(model, resolution="1080p", seconds=15, sound="always", ref_video=False)
    elif model == "kling-3.0":
        low = video_yuan(model, resolution="720p", seconds=3, sound="silent", ref_video=False)
        high = video_yuan(model, resolution="4k", seconds=15, sound="native", ref_video=True)
    elif model == "kling-o1":
        low = video_yuan(model, resolution="720p", seconds=3, sound="silent", ref_video=False)
        high = video_yuan(model, resolution="1080p", seconds=10, sound="silent", ref_video=True)
    elif model == "kling-2.6":
        low = video_yuan(model, resolution="720p", seconds=5, sound="silent", ref_video=False)
        high = video_yuan(model, resolution="1080p", seconds=10, sound="voice", ref_video=False)
    elif model == "kling-2.5-turbo":
        low = video_yuan(model, resolution="720p", seconds=5, sound="silent", ref_video=False)
        high = video_yuan(model, resolution="1080p", seconds=10, sound="silent", ref_video=False)
    else:
        raise ValueError(f"未知可灵视频模型 {model}")
    return cny_to_points(low), cny_to_points(high)


def image_point_bounds(model: str) -> tuple[int, int]:
    if model == "kling-image-3.0":
        low = image_yuan(model, tier="omni", resolution="1k", count=1)
        high = image_yuan(model, tier="omni", resolution="4k", count=9)
    elif model == "kling-image-o1":
        low = image_yuan(model, tier="omni", resolution="1k", count=1)
        high = image_yuan(model, tier="omni", resolution="2k", count=9)
    elif model == "kling-image-2.1":
        low = image_yuan(model, tier="t2i", resolution="1k", count=1)
        high = image_yuan(model, tier="multi", resolution="2k", count=9)
    elif model == "kling-image-common":
        low = image_yuan(model, tier="outpaint", resolution="1k", count=1)
        high = image_yuan(model, tier="outpaint", resolution="1k", count=9)
    else:
        raise ValueError(f"未知可灵图片模型 {model}")
    return cny_to_points(low), cny_to_points(high)


__all__ = [
    "POINTS_PER_CNY",
    "cny_to_points",
    "image_point_bounds",
    "image_yuan",
    "video_point_bounds",
    "video_yuan",
]
