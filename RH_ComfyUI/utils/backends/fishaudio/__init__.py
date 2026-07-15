"""Fish Audio TTS 后端 — 官方 S2 系列语音合成"""

from .api import FishAudioAPI
from .executor import FishAudioAdapter

__all__ = ["FishAudioAPI", "FishAudioAdapter"]
