"""MiniMax 后端 — 图像生成 + T2A 异步语音合成 + H3 视频"""

from .api import MiniMaxAPI
from .executor import MiniMaxBackend
from .h3_channel import MiniMaxH3Channel, builtin_minimax_h3_channels
from .h3_provider import MiniMaxH3Provider

__all__ = [
    "MiniMaxAPI",
    "MiniMaxBackend",
    "MiniMaxH3Channel",
    "MiniMaxH3Provider",
    "builtin_minimax_h3_channels",
]
