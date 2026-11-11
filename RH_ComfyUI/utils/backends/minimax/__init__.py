"""MiniMax 后端 — 图像生成 + T2A 异步语音合成 + H3 视频 + H3 图像"""

from .api import MiniMaxAPI
from .executor import MiniMaxBackend
from .h3_channel import MiniMaxH3Channel, builtin_minimax_h3_channels
from .h3_provider import MiniMaxH3Provider
from .h3_image_channel import MiniMaxH3ImageChannel, builtin_minimax_h3_image_channels
from .h3_image_provider import MiniMaxH3ImageProvider

__all__ = [
    "MiniMaxAPI",
    "MiniMaxBackend",
    "MiniMaxH3Channel",
    "MiniMaxH3Provider",
    "MiniMaxH3ImageChannel",
    "MiniMaxH3ImageProvider",
    "builtin_minimax_h3_channels",
    "builtin_minimax_h3_image_channels",
]
