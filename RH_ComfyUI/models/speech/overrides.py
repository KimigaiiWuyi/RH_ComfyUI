"""语音模态的编程式模型类 — IndexTTS2(参考音频一等公民,蓝图 §10)"""

from __future__ import annotations

from ..bridge import SpeechPipelineModel
from ...core.schema.card import ModelCard
from ...utils.core.pipeline import NodeDef


class IndexTTS2Model(SpeechPipelineModel):
    """IndexTTS2:自然中文 TTS,支持参考音频音色克隆与情绪指令

    参考音频数据流:画布音频节点连线 → HTTP payload.reference_audio
    → GenerationRequest.reference_audio → 本模型 schema 的 reference_audio 端口
    → ComfyUI 工作流 LoadAudio 节点(由既有 mapper 消费)。
    """

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supports_voice_clone = True
        self.supports_mood = True
        self.card = ModelCard(
            description=node.description or "本地 IndexTTS2 语音合成,中文发音准确,支持音色克隆与情绪",
            strengths=["中文自然", "参考音频克隆", "情绪/语气控制", "本地零 API 费"],
            categories=["有声内容", "配音", "数字人语音"],
            weaknesses=["不适合唱歌与方言(建议 mimo_tts)"],
            languages=["zh"],
        )


__all__ = ["IndexTTS2Model"]
