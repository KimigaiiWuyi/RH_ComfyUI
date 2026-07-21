"""Fish Audio ASR 动态积分计价

计价规则:0.36 美元 / 音频小时,1 美元 = 100 积分 → 0.36 * 100 = 36 积分 / 音频小时。
根据输入音频时长计费,时长优先从音频文件元数据(mutagen)读取,失败时按文件大小估算。

point_cost 仅作兜底。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 计费常量 ──

# 0.36 美元 / 音频小时,1 美元 = 100 积分 → 36 积分 / 音频小时
POINTS_PER_AUDIO_HOUR: int = 36

# 每秒积分 = 36 / 3600 = 0.01 积分/秒
POINTS_PER_AUDIO_SECOND: float = POINTS_PER_AUDIO_HOUR / 3600.0

# 估算音频时长时的默认比特率(128 kbps,常见语音 MP3)
_ESTIMATED_BITRATE_BPS: int = 128_000


def _duration_from_mutagen(audio_bytes: bytes) -> Optional[float]:
    """尝试从音频文件元数据读取时长(秒)。

    支持 wav/mp3/opus/ogg/flac 等 mutagen 能解析的格式。
    解析失败返回 None。
    """
    try:
        from mutagen import File as MutagenFile
        from io import BytesIO

        f = MutagenFile(BytesIO(audio_bytes))
        if f is None or f.info is None:
            return None
        duration = getattr(f.info, "length", None)
        if duration and duration > 0:
            return float(duration)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[FishAudio ASR] mutagen 解析音频时长失败,将按文件大小估算: {e}")
    return None


def _duration_from_size(audio_bytes: bytes) -> float:
    """按文件大小估算音频时长(秒)。

    用默认比特率 128 kbps 估算,适用于常见语音 MP3。
    """
    size_bits = len(audio_bytes) * 8
    return size_bits / _ESTIMATED_BITRATE_BPS


def estimate_audio_duration_seconds(audio_bytes: bytes) -> float:
    """估算音频时长(秒)。

    优先从元数据读取,失败时按文件大小估算。
    """
    duration = _duration_from_mutagen(audio_bytes)
    if duration is not None:
        return duration
    return _duration_from_size(audio_bytes)


def calculate_asr_points(audio_bytes: bytes) -> int:
    """根据音频时长计算积分(分),向上取整,最小 1 积分。"""
    duration_seconds = estimate_audio_duration_seconds(audio_bytes)
    points = duration_seconds * POINTS_PER_AUDIO_SECOND
    # 向上取整,最小 1 积分
    return max(int(points) + (1 if points > int(points) else 0), 1)


def estimate_fish_asr_points(audio_bytes: Optional[bytes]) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    Args:
        audio_bytes: 输入音频字节数据,缺失时返回 1 积分(最小值)
    """
    if not audio_bytes:
        return 1
    return calculate_asr_points(audio_bytes)


__all__ = [
    "POINTS_PER_AUDIO_HOUR",
    "POINTS_PER_AUDIO_SECOND",
    "estimate_audio_duration_seconds",
    "calculate_asr_points",
    "estimate_fish_asr_points",
]
