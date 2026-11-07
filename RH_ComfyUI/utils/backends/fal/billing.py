"""fal 积分换算(纯计算,不读配置不发网络,供校验与计费引用)。

FLUX 3 Max 官方按「生成秒数 × 分辨率档」计费(美元):
  720p $0.17/s,1080p $0.29/s,时长整数秒 5~20。
积分换算沿用平台口径 $1 = 100 积分,向上取整、下限 1 分。

官网 2026-11-23 前有阶梯折扣价(75%/50% off)。
折扣是限时的,这里始终按刊例价计——上游打折时平台少付,
刊例价计价对用户仍是正确的上限,不会欠收。
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal

from .catalog import MAX_DURATION, MIN_DURATION

# 积分 ↔ 美元:沿用平台 $1 = 100 积分口径
POINTS_PER_USD = Decimal("100")

# 官方刊例价(美元 / 秒),两档分辨率
RATE_PER_SECOND: dict[str, Decimal] = {
    "720p": Decimal("0.17"),
    "1080p": Decimal("0.29"),
}


def usd_to_points(usd: Decimal) -> int:
    cents = (usd * POINTS_PER_USD).quantize(Decimal("1"), rounding=ROUND_UP)
    return max(int(cents), 1)


def video_usd(resolution: str, seconds: int | float | str) -> Decimal:
    res = resolution.strip().lower()
    if res not in RATE_PER_SECOND:
        raise ValueError(f"flux3max 未知分辨率: {resolution}")
    secs = Decimal(str(seconds))
    if secs <= 0:
        raise ValueError(f"flux3max 时长必须为正: {seconds}")
    return RATE_PER_SECOND[res] * secs


def video_point_bounds() -> tuple[int, int]:
    """(min, max):5s 720p 下限,20s 1080p 上限。"""
    low = usd_to_points(video_usd("720p", MIN_DURATION))
    high = usd_to_points(video_usd("1080p", MAX_DURATION))
    return low, max(high, low + 1)
