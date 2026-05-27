"""MiniMax 后端 — 图像生成 + T2A 异步语音合成"""

from .api import MiniMaxAPI
from .executor import MiniMaxBackend

__all__ = ["MiniMaxAPI", "MiniMaxBackend"]
