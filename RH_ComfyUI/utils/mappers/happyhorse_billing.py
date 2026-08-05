"""HappyHorse 1.1 视频生成动态积分计价

按输出时长(秒)×分辨率档位计费。费率取自公开渠道近似值
(约 $0.10–0.18/s,按 1 元=100 积分换算后取整),后续可按官方价目表微调。

point_cost 仅作兜底。
"""

from __future__ import annotations

from typing import Optional

# 元 → 积分
_YUAN_TO_POINTS: int = 100

# 分辨率 → 元/秒
_RATES_YUAN_PER_SEC: dict[str, float] = {
    "480p": 0.60,
    "720p": 1.00,
    "1080p": 1.50,
}

_DEFAULT_RESOLUTION = "720p"
_DEFAULT_DURATION = 5
_MIN_DURATION = 3
_MAX_DURATION = 15


def _norm_resolution(resolution: Optional[str]) -> str:
    if not resolution:
        return _DEFAULT_RESOLUTION
    r = resolution.strip().lower()
    if r.endswith("p") and r[:-1].isdigit():
        return r
    # 720P → 720p
    if r[:-1].isdigit() and r.endswith("p"):
        return r
    return _DEFAULT_RESOLUTION


def calculate_happyhorse_points(
    resolution: Optional[str] = None,
    duration: Optional[float] = None,
) -> int:
    """纯函数:分辨率 × 时长 → 积分,最小 1。"""
    res = _norm_resolution(resolution)
    rate = _RATES_YUAN_PER_SEC.get(res, _RATES_YUAN_PER_SEC[_DEFAULT_RESOLUTION])
    try:
        dur = float(duration) if duration is not None else float(_DEFAULT_DURATION)
    except (TypeError, ValueError):
        dur = float(_DEFAULT_DURATION)
    dur = max(_MIN_DURATION, min(_MAX_DURATION, dur))
    yuan = rate * dur
    points = yuan * _YUAN_TO_POINTS
    return max(int(points) + (1 if points > int(points) else 0), 1)


def estimate_happyhorse_points(
    resolution: Optional[str] = None,
    duration: Optional[float] = None,
) -> int:
    """薄壳:供 estimate_cost 调用。"""
    return calculate_happyhorse_points(resolution, duration)


__all__ = [
    "calculate_happyhorse_points",
    "estimate_happyhorse_points",
]
