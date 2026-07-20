"""models.asr.overrides — ASR 模态的编程式模型类

ASR 模态没有 emotion / 音色克隆这些 TTS 专属概念,只有「音频 → 文本」一个动作;
基类 AsrGenerationBase 已经把 audio / language / include_timestamps 等通用约束
集中声明,子类只需要挑合适的 ModelCard 描述与优先参数。
"""

from __future__ import annotations

from ..bridge import AsrPipelineModel
from ...core.schema.card import ModelCard
from ...utils.core.pipeline import NodeDef


class FishAsrModel(AsrPipelineModel):
    """Fish Audio ASR:多语言自动识别,带时间戳分段,无音色/情绪概念"""

    # 不支持情绪 / 不支持克隆(语义不适用)
    supports_mood = False
    supports_voice_clone = False

    # Fish Audio 文档没明列语言白名单,留空表示交给上游自动识别
    supported_languages: list[str] = []

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.card = ModelCard(
            description=node.description or "Fish Audio ASR:多语言语音识别,带时间戳分段,适合口播/字幕/转写",
            strengths=["多语言自动识别", "时间戳分段", "wav/mp3/opus 原样接受"],
            categories=["语音识别", "字幕生成", "口播转写"],
            languages=["zh", "en", "ja"],
        )


__all__ = ["FishAsrModel"]
