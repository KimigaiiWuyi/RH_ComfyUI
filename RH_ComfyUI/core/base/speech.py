"""DigitalHumanSpeechBase — 数字人语音 / TTS 模态基类

关键设计:reference_audio(参考音色)是模态级一等端口:
- 基类 base_speech_schema() 骨架里按能力开关包含 reference_audio 端口
- 不支持克隆的模型声明 supports_voice_clone=False,端口即从 schema 消失
- 前端(无限画布)据 schema 中是否存在 reference_audio 端口决定是否渲染音频连线口
"""

from __future__ import annotations

from .errors import ValidationError
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
                description="参考音频:用于克隆音色,画布上可由音频节点连线提供",
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

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if len(request.prompt) > self.max_text_length:
            raise ValidationError(
                f"文本长度 {len(request.prompt)} 超过 {self.display_name} 上限 {self.max_text_length} 字"
            )
        if request.reference_audio is not None and not self.supports_voice_clone:
            raise ValidationError(
                f"{self.display_name} 不支持参考音频克隆;请换用支持克隆的模型(如 IndexTTS2 / MiniMax T2A)"
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
