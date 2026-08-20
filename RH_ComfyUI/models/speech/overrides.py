"""语音模态的编程式模型类 — 参考音频一等公民 + 各家情绪风格声明

情绪处理已上移到基类(DigitalHumanSpeechBase.normalize),这里各模型只声明
emotion_style(内联/自然语言/枚举),新增模型挑一个风格即可,无需重写逻辑。
"""

from __future__ import annotations

from ..bridge import SpeechPipelineModel
from ...core.schema.card import ModelCard
from ...core.base.emotion import EmotionStyle
from ...utils.core.pipeline import NodeDef
from ...utils.backends.minimax.config import (
    minimax_disabled_reason,
    is_minimax_model_enabled,
)


class IndexTTS2Model(SpeechPipelineModel):
    """IndexTTS2:自然中文 TTS,支持参考音频音色克隆与情绪指令

    参考音频数据流:音频输入口 → HTTP payload.reference_audio
    → GenerationRequest.reference_audio → 本模型 schema 的 reference_audio 端口
    → 工作流的 LoadAudio 节点(由既有 mapper 消费)。
    """

    emotion_style = EmotionStyle.NATURAL_LANGUAGE

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supports_voice_clone = True
        self.supports_mood = True
        self.card = ModelCard(
            description=node.description or "本地 IndexTTS2 语音合成,中文发音准确,支持音色克隆与情绪",
            strengths=["中文自然", "参考音频克隆", "情绪/语气控制", "本地零 API 费"],
            categories=["有声内容", "配音", "数字人语音"],
            weaknesses=["不适合唱歌与方言"],
            languages=["zh"],
        )


class IndexTTS25Model(SpeechPipelineModel):
    """IndexTTS2.5: RunningHub AI 应用上的 IndexTTS 2.5

    参考音频数据流:音频输入口 → HTTP payload.reference_audio
    → GenerationRequest.reference_audio → schema 的 reference_audio 端口
    → rh_app 声明式映射 type=upload_audio,写入 AI 应用节点 2.audio。
    情绪走独立自由文本(节点 8.text),如「开心」「高兴」。
    """

    emotion_style = EmotionStyle.NATURAL_LANGUAGE

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supports_voice_clone = True
        self.supports_mood = True
        self.card = ModelCard(
            description=node.description or "IndexTTS2.5 云端语音合成,中文发音准确,支持音色克隆与情绪",
            strengths=["中文自然", "参考音频克隆", "情绪/语气控制", "云端无需本地 GPU"],
            categories=["有声内容", "配音", "数字人语音"],
            weaknesses=["不适合唱歌与方言"],
            languages=["zh"],
        )


class MinimaxSpeechModel(SpeechPipelineModel):
    """MiniMax T2A:情绪走固定枚举 —— 枚举外的情绪(含中文自由描述)自动收敛/丢弃"""

    emotion_style = EmotionStyle.ENUM
    emotion_enum = [
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "calm",
        "fluent",
        "whisper",
    ]

    async def check_available(self) -> bool:
        if not is_minimax_model_enabled(self.name):
            return False
        return await super().check_available()

    async def unavailable_reason(self) -> str:
        disabled = minimax_disabled_reason(self.name, self.display_name)
        if disabled is not None:
            return disabled
        return await super().unavailable_reason()


class FishTtsModel(SpeechPipelineModel):
    """Fish Audio S2:情绪用正文内联 [tag],支持句中定位与叠加;自动音色克隆"""

    emotion_style = EmotionStyle.INLINE_BRACKET

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.card = ModelCard(
            description=node.description or "Fish Audio S2 语音合成,多语言,内联情绪细粒度可控,自动音色克隆",
            strengths=["多语言", "内联情绪细粒度可控", "自动音色克隆", "自然韵律"],
            categories=["有声内容", "配音", "数字人语音"],
            languages=["zh", "en", "ja"],
        )


__all__ = ["IndexTTS2Model", "IndexTTS25Model", "MinimaxSpeechModel", "FishTtsModel"]
