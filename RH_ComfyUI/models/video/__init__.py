"""models.video — 视频模态的编程式模型定义"""

from .defs import ALL_MODELS
from .overrides import (
    Wan22VideoModel,
    Wan30VideoModel,
    SeedanceVideoModel,
    MiniMaxH3VideoModel,
    HappyHorseVideoModel,
    Seedance25VideoModel,
)

__all__ = [
    "ALL_MODELS",
    "SeedanceVideoModel",
    "Seedance25VideoModel",
    "Wan22VideoModel",
    "Wan30VideoModel",
    "HappyHorseVideoModel",
    "MiniMaxH3VideoModel",
]
