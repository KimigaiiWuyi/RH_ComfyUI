"""DigitalHumanSpeechBase — 数字人语音 / TTS 模态基类

关键设计:reference_audio(参考音色)是模态级一等端口:
- 基类 base_speech_schema() 骨架里按能力开关包含 reference_audio 端口
- 不支持克隆的模型声明 supports_voice_clone=False,端口即从 schema 消失
- 调用方据 schema 中是否存在 reference_audio 端口决定是否暴露音频输入口

情绪归一化在基类统一处理:每个模型只声明 emotion_style(内联/自然语言/枚举/无),
normalize() 按风格把(正文, 情绪)整形成上游能直接消费的形态。新增模型无需重写
情绪逻辑,只挑一个风格即可(见 core/base/emotion.py)。
"""

from __future__ import annotations

from .errors import ValidationError
from .emotion import (
    EmotionStyle,
    to_inline_tag,
    to_enum_emotion,
    strip_inline_tags,
    translate_inline_zh_tags,
)
from .generation import AIGCGenerationBase
from ..schema.types import PortSpec, PortType
from ..schema.request import TaskType, GenerationRequest


class DigitalHumanSpeechBase(AIGCGenerationBase):
    """数字人语音(TTS / 音色克隆 / 情绪控制)模态基类"""

    modality: TaskType = TaskType.SPEECH

    # ── 模态级能力开关 ──
    supports_voice_clone: bool = False  # 是否接受 reference_audio
    supports_mood: bool = False  # 是否接受情绪/风格指令
    builtin_voices: list[str] = []  # 预置音色 id 列表(空=无预置音色)
    has_default_voice: bool = True  # 未提供参考音频时是否有内置默认音色
    reference_audio_formats: list[str] = ["audio/mpeg", "audio/wav"]
    max_text_length: int = 2000

    # ── 情绪风格(子类覆盖;默认自然语言,即情绪走独立字段、正文内联标记剥离) ──
    emotion_style: EmotionStyle = EmotionStyle.NATURAL_LANGUAGE
    emotion_enum: list[str] = []  # 仅 ENUM 风格用:zh→en 后需收敛到的枚举集合

    def base_speech_schema(self) -> dict[str, PortSpec]:
        """模态骨架 schema:子类在此基础上增删改"""
        schema: dict[str, PortSpec] = {
            "prompt": PortSpec(
                type=PortType.TEXT,
                required=True,
                description=f"待合成文本(≤{self.max_text_length} 字)",
            ),
        }
        if self.supports_voice_clone:
            schema["reference_audio"] = PortSpec(
                type=PortType.AUDIO,
                required=False,
                mime_types=list(self.reference_audio_formats),
                description="参考音频:传入即克隆音色,可由音频输入口连线提供",
            )
        if self.supports_mood:
            schema["mood"] = PortSpec(type=PortType.STRING, required=False, description="情绪/风格指令")
        if self.builtin_voices:
            schema["voice_id"] = PortSpec(
                type=PortType.ENUM,
                values=list(self.builtin_voices),
                required=False,
                description="预置音色",
            )
        return schema

    def input_schema(self) -> dict[str, PortSpec]:
        return self.base_speech_schema()

    def normalize(self, request: GenerationRequest) -> GenerationRequest:
        """在基类统一做情绪整形,再交给各自 mapper(mapper 只读归一后的字段)"""
        request = super().normalize(request)
        return self._apply_emotion(request)

    def _apply_emotion(self, request: GenerationRequest) -> GenerationRequest:
        """按 emotion_style 把(prompt, mood)整形成上游能直接消费的形态

        - inline_bracket:正文 zh→en 翻译 + 结构化情绪并入句首,mood 清空(已进正文)
        - enum:剥离正文内联标记 + 结构化情绪/剥出标记收敛到枚举
        - natural_language:剥离正文内联标记;无结构化情绪时用剥出的首个标记兜底
        - none / 不支持情绪:剥离正文内联标记并清空 mood
        """
        style = self.emotion_style if self.supports_mood else EmotionStyle.NONE

        if style is EmotionStyle.INLINE_BRACKET:
            text = translate_inline_zh_tags(request.prompt)
            if request.mood:
                text = f"{to_inline_tag(request.mood)} {text}"
            request.prompt = text
            request.mood = None
            return request

        cleaned, stripped = strip_inline_tags(request.prompt)
        request.prompt = cleaned

        if style is EmotionStyle.ENUM:
            request.mood = to_enum_emotion(request.mood, stripped, self.emotion_enum)
        elif style is EmotionStyle.NATURAL_LANGUAGE:
            if not request.mood and stripped:
                request.mood = stripped[0]
        else:  # NONE
            request.mood = None
        return request

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if len(request.prompt) > self.max_text_length:
            raise ValidationError(
                f"文本长度 {len(request.prompt)} 超过 {self.display_name} 上限 {self.max_text_length} 字"
            )
        if request.reference_audio is not None and not self.supports_voice_clone:
            raise ValidationError(
                f"{self.display_name} 不支持参考音频克隆;请换用支持克隆的模型"
            )
        if (
            request.reference_audio is None
            and self.supports_voice_clone
            and not self.builtin_voices
            and not self.has_default_voice
        ):
            # 无预置音色且未提供参考音频的模型,必须给出明确指引而非产出随机音色
            raise ValidationError(f"{self.display_name} 需要提供参考音频(reference_audio)以确定音色")


__all__ = ["DigitalHumanSpeechBase"]
