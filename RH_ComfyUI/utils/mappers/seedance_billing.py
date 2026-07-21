"""Seedance 系列视频生成动态积分计价

计价规则(取自官方文档):
  token 用量估算公式:(输入视频时长 + 输出视频时长) × 输出视频宽 × 输出视频高 × 输出视频帧率 / 1024

  doubao-seedance-2.0:
    480p/720p: 无输入视频 46 元/M tokens,有输入视频 28 元/M tokens
    1080p:     无输入视频 51 元/M tokens,有输入视频 31 元/M tokens
    4K:        无输入视频 26 元/M tokens,有输入视频 16 元/M tokens

  doubao-seedance-2.0-fast (仅 480p/720p):
    无输入视频 37 元/M tokens,有输入视频 22 元/M tokens

  doubao-seedance-2.0-mini (仅 480p/720p):
    无输入视频 23 元/M tokens,有输入视频 14 元/M tokens

  doubao-seedance-1.5-pro:
    有声视频 16 元/M tokens,无声视频 8 元/M tokens

  doubao-seedance-1.0-pro:
    15 元/M tokens

point_cost 仅作兜底。
"""

from __future__ import annotations

from typing import Optional

# ── 计费常量 ──

# 元 → 积分换算:1 元 = 100 积分
_YUAN_TO_POINTS: int = 100

# ── 分辨率 → 像素尺寸 + 帧率 ──

_RESOLUTION_SPECS: dict[str, tuple[int, int, int]] = {
    # resolution: (width, height, fps)
    "480p": (854, 480, 24),
    "720p": (1280, 720, 24),
    "1080p": (1920, 1080, 24),
    "4k": (3840, 2160, 24),
    "4K": (3840, 2160, 24),
}

# ── Seedance 2.0 费率(元/M tokens) ──

_SEEDANCE2_RATES = {
    # resolution: (无输入视频费率, 有输入视频费率)
    "480p": (46.00, 28.00),
    "720p": (46.00, 28.00),
    "1080p": (51.00, 31.00),
    "4k": (26.00, 16.00),
    "4K": (26.00, 16.00),
}

# ── Seedance 2.0 Fast 费率(元/M tokens,仅 480p/720p) ──

_SEEDANCE2_FAST_RATES = {
    "480p": (37.00, 22.00),
    "720p": (37.00, 22.00),
}

# ── Seedance 2.0 Mini 费率(元/M tokens,仅 480p/720p) ──

_SEEDANCE2_MINI_RATES = {
    "480p": (23.00, 14.00),
    "720p": (23.00, 14.00),
}

# ── Seedance 1.5 Pro 费率(元/M tokens) ──

_SEEDANCE15_PRO_WITH_AUDIO: float = 16.00
_SEEDANCE15_PRO_WITHOUT_AUDIO: float = 8.00

# ── Seedance 1.0 Pro 费率(元/M tokens) ──

_SEEDANCE10_PRO_RATE: float = 15.00

# ── 输入视频时长估算(秒) ──

_DEFAULT_INPUT_VIDEO_DURATION: float = 5.0


def _calculate_tokens(
    input_duration: float,
    output_duration: float,
    width: int,
    height: int,
    fps: int,
) -> float:
    """计算 token 用量。

    公式:(输入视频时长 + 输出视频时长) × 输出视频宽 × 输出视频高 × 输出视频帧率 / 1024
    """
    return (input_duration + output_duration) * width * height * fps / 1024


def _tokens_to_points(tokens: float, rate_yuan_per_million: float) -> int:
    """将 token 数转换为积分(向上取整,最小 1 积分)。

    rate_yuan_per_million:每百万 token 的费率(元)
    """
    points_per_million = rate_yuan_per_million * _YUAN_TO_POINTS
    points = tokens * points_per_million / 1_000_000
    return max(int(points) + (1 if points > int(points) else 0), 1)


def _get_resolution_spec(resolution: str) -> tuple[int, int, int]:
    """获取分辨率对应的(宽, 高, 帧率)。

    未匹配时默认 720p。
    """
    return _RESOLUTION_SPECS.get(resolution, _RESOLUTION_SPECS["720p"])


def _has_input_video(video_refs: list) -> bool:
    """判断是否包含输入视频。"""
    return bool(video_refs) and len(video_refs) > 0


def estimate_seedance2_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
) -> int:
    """Seedance 2.0 估算积分。

    Args:
        resolution: 输出分辨率(480p/720p/1080p/4k)
        output_duration: 输出视频时长(秒)
        video_refs: 输入视频参考列表
    """
    width, height, fps = _get_resolution_spec(resolution)
    has_input = _has_input_video(video_refs or [])
    input_duration = _DEFAULT_INPUT_VIDEO_DURATION if has_input else 0.0

    tokens = _calculate_tokens(input_duration, output_duration, width, height, fps)
    rate = _SEEDANCE2_RATES.get(resolution, _SEEDANCE2_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance2_fast_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
) -> int:
    """Seedance 2.0 Fast 估算积分(仅支持 480p/720p)。"""
    width, height, fps = _get_resolution_spec(resolution)
    has_input = _has_input_video(video_refs or [])
    input_duration = _DEFAULT_INPUT_VIDEO_DURATION if has_input else 0.0

    tokens = _calculate_tokens(input_duration, output_duration, width, height, fps)
    rate = _SEEDANCE2_FAST_RATES.get(resolution, _SEEDANCE2_FAST_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance2_mini_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
) -> int:
    """Seedance 2.0 Mini 估算积分(仅支持 480p/720p)。"""
    width, height, fps = _get_resolution_spec(resolution)
    has_input = _has_input_video(video_refs or [])
    input_duration = _DEFAULT_INPUT_VIDEO_DURATION if has_input else 0.0

    tokens = _calculate_tokens(input_duration, output_duration, width, height, fps)
    rate = _SEEDANCE2_MINI_RATES.get(resolution, _SEEDANCE2_MINI_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance15_pro_points(
    resolution: str,
    output_duration: float,
    generate_audio: bool = True,
    video_refs: Optional[list] = None,
) -> int:
    """Seedance 1.5 Pro 估算积分。

    有声视频 16 元/M,无声视频 8 元/M。
    """
    width, height, fps = _get_resolution_spec(resolution)
    has_input = _has_input_video(video_refs or [])
    input_duration = _DEFAULT_INPUT_VIDEO_DURATION if has_input else 0.0

    tokens = _calculate_tokens(input_duration, output_duration, width, height, fps)
    rate_yuan = _SEEDANCE15_PRO_WITH_AUDIO if generate_audio else _SEEDANCE15_PRO_WITHOUT_AUDIO

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance10_pro_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
) -> int:
    """Seedance 1.0 Pro 估算积分(固定 15 元/M tokens)。"""
    width, height, fps = _get_resolution_spec(resolution)
    has_input = _has_input_video(video_refs or [])
    input_duration = _DEFAULT_INPUT_VIDEO_DURATION if has_input else 0.0

    tokens = _calculate_tokens(input_duration, output_duration, width, height, fps)
    return _tokens_to_points(tokens, _SEEDANCE10_PRO_RATE)


__all__ = [
    "estimate_seedance2_points",
    "estimate_seedance2_fast_points",
    "estimate_seedance2_mini_points",
    "estimate_seedance15_pro_points",
    "estimate_seedance10_pro_points",
    "_calculate_tokens",
    "_tokens_to_points",
    "_get_resolution_spec",
    "_DEFAULT_INPUT_VIDEO_DURATION",
    "_SEEDANCE2_RATES",
    "_SEEDANCE2_FAST_RATES",
    "_SEEDANCE2_MINI_RATES",
    "_SEEDANCE15_PRO_WITH_AUDIO",
    "_SEEDANCE15_PRO_WITHOUT_AUDIO",
    "_SEEDANCE10_PRO_RATE",
]
