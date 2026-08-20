"""models.speech — 语音模态的编程式模型定义"""

from .defs import ALL_MODELS
from .overrides import FishTtsModel, IndexTTS2Model, IndexTTS25Model, MinimaxSpeechModel

__all__ = ["ALL_MODELS", "IndexTTS2Model", "IndexTTS25Model", "MinimaxSpeechModel", "FishTtsModel"]
