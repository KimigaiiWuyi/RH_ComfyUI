"""models.video — 视频模态的编程式模型定义"""

from .defs import ALL_MODELS
from .overrides import (
    Wan22VideoModel,
    SeedanceVideoModel,
    HappyHorseVideoModel,
    Seedance25VideoModel,
)

__all__ = [
    "ALL_MODELS",
    "SeedanceVideoModel",
    "Seedance25VideoModel",
    "Wan22VideoModel",
    "HappyHorseVideoModel",
]
