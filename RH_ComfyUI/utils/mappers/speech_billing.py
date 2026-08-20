"""语音合成动态积分计价(UTF-8 字节计费)

计价规则(取自官方文档):
  - Fish Audio S2 (s2.1-pro):15 美元 / M UTF-8 bytes → 1500 积分 / M bytes
  - IndexTTS2 / IndexTTS2.5: 5 美元 / M UTF-8 bytes →  500 积分 / M bytes

根据输入文本的 UTF-8 字节长度计费。中文在 UTF-8 下通常 3 字节/字。
point_cost 仅作兜底。
"""

from __future__ import annotations

from typing import Optional

# ── 计费常量 ──

# Fish Audio S2:15 美元 / M UTF-8 bytes,1 美元 = 100 积分 → 1500 积分 / M bytes
FISHAUDIO_POINTS_PER_MILLION_BYTES: int = 1500

# IndexTTS2 / IndexTTS2.5:5 美元 / M UTF-8 bytes,1 美元 = 100 积分 → 500 积分 / M bytes
INDEX_TTS2_POINTS_PER_MILLION_BYTES: int = 500


def _points_by_bytes(byte_length: int, points_per_million_bytes: int) -> int:
    """按 UTF-8 字节长度计算积分(分),向上取整,最小 1 积分。"""
    if byte_length <= 0:
        return 1
    points = (byte_length * points_per_million_bytes + 999_999) // 1_000_000
    return max(points, 1)


def calculate_fish_tts_points(text: Optional[str]) -> int:
    """Fish Audio S2:按输入文本 UTF-8 字节长度计算积分。"""
    if not text:
        return 1
    byte_length = len(text.encode("utf-8"))
    return _points_by_bytes(byte_length, FISHAUDIO_POINTS_PER_MILLION_BYTES)


def calculate_index_tts2_points(text: Optional[str]) -> int:
    """IndexTTS2:按输入文本 UTF-8 字节长度计算积分。"""
    if not text:
        return 1
    byte_length = len(text.encode("utf-8"))
    return _points_by_bytes(byte_length, INDEX_TTS2_POINTS_PER_MILLION_BYTES)


def estimate_fish_tts_points(text: Optional[str]) -> int:
    """Fish Audio S2 估算积分(供 estimate_cost 调用)。"""
    return calculate_fish_tts_points(text)


def estimate_index_tts2_points(text: Optional[str]) -> int:
    """IndexTTS2 估算积分(供 estimate_cost 调用)。"""
    return calculate_index_tts2_points(text)


__all__ = [
    "FISHAUDIO_POINTS_PER_MILLION_BYTES",
    "INDEX_TTS2_POINTS_PER_MILLION_BYTES",
    "calculate_fish_tts_points",
    "calculate_index_tts2_points",
    "estimate_fish_tts_points",
    "estimate_index_tts2_points",
]
