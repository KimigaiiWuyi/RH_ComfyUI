"""MiniMax H3 视频生成动态积分计价

官方按量(人民币,2026-08 文档):
  - 768P:0.50 元 / 秒
  - 2K:  0.80 元 / 秒
  计量秒数 = 输出秒数 + 输入参考视频秒数。

1 元 = 100 积分;向上取整,最小 1。
point_cost 仅作兜底(≈ 2K × 5s = 400)。
"""

from __future__ import annotations

from typing import Any, Optional

_YUAN_TO_POINTS: int = 100

# 分辨率 → 元/秒
_RATES_YUAN_PER_SEC: dict[str, float] = {
    "768p": 0.50,
    "2k": 0.80,
}

_DEFAULT_RESOLUTION = "2k"
_DEFAULT_DURATION = 5
_MIN_DURATION = 4
_MAX_DURATION = 15


def _norm_resolution(resolution: Optional[str]) -> str:
    if not resolution:
        return _DEFAULT_RESOLUTION
    r = resolution.strip().lower()
    if r in ("768p", "768"):
        return "768p"
    if r in ("2k",):
        return "2k"
    if r.endswith("p") and r[:-1].isdigit():
        # 误传 720p 等 → 按短边就近到 768p
        return "768p"
    return _DEFAULT_RESOLUTION


def _clamp_duration(duration: Optional[float]) -> float:
    try:
        dur = float(duration) if duration is not None else float(_DEFAULT_DURATION)
    except (TypeError, ValueError):
        dur = float(_DEFAULT_DURATION)
    return max(_MIN_DURATION, min(_MAX_DURATION, dur))


def calculate_minimax_h3_points(
    resolution: Optional[str] = None,
    duration: Optional[float] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """纯函数: (输出秒 + 输入参考视频秒) × 档位单价。"""
    res = _norm_resolution(resolution)
    rate = _RATES_YUAN_PER_SEC.get(res, _RATES_YUAN_PER_SEC[_DEFAULT_RESOLUTION])
    out_s = _clamp_duration(duration)
    try:
        in_s = float(input_video_duration or 0.0)
    except (TypeError, ValueError):
        in_s = 0.0
    in_s = max(0.0, in_s)
    yuan = rate * (out_s + in_s)
    points = yuan * _YUAN_TO_POINTS
    return max(int(points) + (1 if points > int(points) else 0), 1)


def estimate_minimax_h3_points(
    resolution: Optional[str] = None,
    duration: Optional[float] = None,
    *,
    video_refs: Optional[list[Any]] = None,
    input_video_duration: Optional[float] = None,
) -> int:
    """薄壳:优先显式 input_video_duration,否则按参考视频段数 × 5s 估。"""
    inp = input_video_duration
    if inp is None and video_refs:
        inp = 5.0 * len(video_refs)
    return calculate_minimax_h3_points(
        resolution,
        duration,
        input_video_duration=inp,
    )


__all__ = [
    "calculate_minimax_h3_points",
    "estimate_minimax_h3_points",
]
