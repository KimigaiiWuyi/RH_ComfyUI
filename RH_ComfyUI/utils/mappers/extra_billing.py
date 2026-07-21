"""额外模型动态积分计价(Wan2.2 / MiMo TTS / MiniMax T2A Speech)

计价规则:
  - wan2.2_videogen:0.6 元/秒 = 60 积分/秒(按输出视频时长计费)
  - mimo_tts (MiMo-V2-TTS):6 美元 / M UTF-8 bytes → 600 积分/M bytes
  - minimax_t2a_speech (speech-2.8-hd):3.5 元/万字符 = 350 积分/万字符

point_cost 仅作兜底。
"""

from __future__ import annotations

from typing import Optional

# ── 计费常量 ──

# Wan2.2:0.6 元/秒 = 60 积分/秒
WAN22_POINTS_PER_SECOND: int = 60

# MiMo TTS:6 美元 / M UTF-8 bytes → 600 积分/M bytes
MIMO_POINTS_PER_MILLION_BYTES: int = 600

# MiniMax T2A Speech:3.5 元/万字符 = 350 积分/万字符
MINIMAX_T2A_POINTS_PER_10K_CHARS: int = 350


# ── Wan2.2 视频生成 ──

def calculate_wan22_points(duration: float) -> int:
    """Wan2.2:按输出视频时长计费,0.6 元/秒 = 60 积分/秒。"""
    if duration <= 0:
        return 1
    points = int(duration * WAN22_POINTS_PER_SECOND)
    return max(points, 1)


def estimate_wan22_points(duration: float) -> int:
    """Wan2.2 估算积分(供 estimate_cost 调用)。"""
    return calculate_wan22_points(duration)


# ── MiMo TTS ──

def calculate_mimo_tts_points(text: Optional[str]) -> int:
    """MiMo TTS:按输入文本 UTF-8 字节长度计费,6 美元/M bytes → 600 积分/M bytes。"""
    if not text:
        return 1
    byte_length = len(text.encode("utf-8"))
    points = (byte_length * MIMO_POINTS_PER_MILLION_BYTES + 999_999) // 1_000_000
    return max(points, 1)


def estimate_mimo_tts_points(text: Optional[str]) -> int:
    """MiMo TTS 估算积分(供 estimate_cost 调用)。"""
    return calculate_mimo_tts_points(text)


# ── MiniMax T2A Speech ──

def calculate_minimax_t2a_points(text: Optional[str]) -> int:
    """MiniMax T2A Speech:按输入文本字符数计费,3.5 元/万字符 = 350 积分/万字符。

    注意:这里按字符数(非字节)计费,中文每字 1 字符。
    """
    if not text:
        return 1
    char_count = len(text)
    # 每万字符 350 积分,向上取整
    points = (char_count * MINIMAX_T2A_POINTS_PER_10K_CHARS + 9999) // 10_000
    return max(points, 1)


def estimate_minimax_t2a_points(text: Optional[str]) -> int:
    """MiniMax T2A Speech 估算积分(供 estimate_cost 调用)。"""
    return calculate_minimax_t2a_points(text)


__all__ = [
    "WAN22_POINTS_PER_SECOND",
    "MIMO_POINTS_PER_MILLION_BYTES",
    "MINIMAX_T2A_POINTS_PER_10K_CHARS",
    "calculate_wan22_points",
    "estimate_wan22_points",
    "calculate_mimo_tts_points",
    "estimate_mimo_tts_points",
    "calculate_minimax_t2a_points",
    "estimate_minimax_t2a_points",
]
