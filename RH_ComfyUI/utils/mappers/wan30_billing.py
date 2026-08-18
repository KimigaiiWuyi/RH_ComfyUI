"""万相 3.0(wan3.0-video)动态积分计价

官方按量(人民币,华北2 北京,2026-08 文档):
  - 480P:0.3 元 / 秒
  - 720P:0.6 元 / 秒
  - 1080P:1.2 元 / 秒
开关声音价格相同;按输出时长计费(30s×1080P ≈ 36 元)。

1 元 = 100 积分;向上取整,最小 1。
point_cost 仅作兜底(≈ 1080p × 5s = 600)。
"""

from __future__ import annotations

from typing import Optional

_YUAN_TO_POINTS: int = 100

_RATES_YUAN_PER_SEC: dict[str, float] = {
    "480p": 0.3,
    "720p": 0.6,
    "1080p": 1.2,
}

_DEFAULT_RESOLUTION = "1080p"
_DEFAULT_DURATION = 5
_MIN_DURATION = 2
_MAX_DURATION = 30
# duration=-1(智能时长)无法预知输出秒数,按 15s 预留
_AUTO_DURATION_ESTIMATE = 15


def _norm_resolution(resolution: Optional[str]) -> str:
    if not resolution:
        return _DEFAULT_RESOLUTION
    r = resolution.strip().lower()
    if r.endswith("p") and r[:-1].isdigit():
        key = f"{r[:-1]}p"
        if key in _RATES_YUAN_PER_SEC:
            return key
    return _DEFAULT_RESOLUTION


def _clamp_duration(duration: Optional[float]) -> float:
    try:
        dur = float(duration) if duration is not None else float(_DEFAULT_DURATION)
    except (TypeError, ValueError):
        dur = float(_DEFAULT_DURATION)
    if dur < 0:
        return float(_AUTO_DURATION_ESTIMATE)
    return max(_MIN_DURATION, min(_MAX_DURATION, dur))


def calculate_wan30_points(
    resolution: Optional[str] = None,
    duration: Optional[float] = None,
) -> int:
    """纯函数:分辨率 × 输出时长 → 积分,最小 1。"""
    res = _norm_resolution(resolution)
    rate = _RATES_YUAN_PER_SEC.get(res, _RATES_YUAN_PER_SEC[_DEFAULT_RESOLUTION])
    out_s = _clamp_duration(duration)
    yuan = rate * out_s
    points = yuan * _YUAN_TO_POINTS
    return max(int(points) + (1 if points > int(points) else 0), 1)


def estimate_wan30_points(
    resolution: Optional[str] = None,
    duration: Optional[float] = None,
) -> int:
    """薄壳:供 estimate_cost 调用。"""
    return calculate_wan30_points(resolution, duration)


__all__ = [
    "calculate_wan30_points",
    "estimate_wan30_points",
]
