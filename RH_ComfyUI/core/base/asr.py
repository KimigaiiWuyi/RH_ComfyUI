"""AsrGenerationBase — 语音识别 / 音频转写模态基类

ASR 与 TTS(SPEECH)互为逆向:
- 输入为音频(单条参考音频 reference_audio 或 audio_refs 列表);
- 输出为文本(主产物 .data 为 UTF-8 字节;segments/duration 等结构化字段
  落在 .outputs / .metadata / .raw 中)。

参考音频的统一约定:
- 优先用 ``reference_audio``(bytes)作为单条输入;若为空再回退到
  ``audio_refs[0]``;两者都缺则校验失败。
- ASR 不做音色克隆,因此不像 DigitalHumanSpeechBase 那样把 reference_audio
  当作音色入口 —— 它的语义是「待转写的音频」。

新增 ASR 模型只需:
1. 继承本类并实现 ``execute_on_channel``;
2. 在 ``input_schema()`` 中声明 ``audio`` / ``language`` 等端口;
3. 在子类的 ``point_cost`` 上写明积分。
无需重写 emotion / 多参考等通用逻辑 —— 本模态没有这些东西。
"""

from __future__ import annotations

from .errors import ValidationError
from .generation import AIGCGenerationBase
from ..schema.types import PortSpec, PortType
from ..schema.request import TaskType, GenerationRequest


class AsrGenerationBase(AIGCGenerationBase):
    """语音识别(ASR)模态基类"""

    modality: TaskType = TaskType.ASR

    # ── 模态级能力开关 ──
    # 默认接收 wav/mp3/opus(m4a 也常见但嗅探时归到 audio/mpeg)
    audio_formats: list[str] = ["audio/mpeg", "audio/wav", "audio/ogg", "audio/opus"]
    max_audio_bytes: int = 20 * 1024 * 1024  # 20MB,跟 Fish Audio /v1/asr 文档上限对齐
    max_audio_seconds: int = 60 * 60  # 60min,同上;实际可按上游放宽

    # 支持的 ISO 639-1 语言码(留空 = 不约束,由上游自动识别)
    supported_languages: list[str] = []

    # 是否默认输出带时间戳的分段
    include_timestamps_default: bool = True

    def base_asr_schema(self) -> dict[str, PortSpec]:
        """模态骨架 schema:子类在此基础上增删改

        端口约定:
        - ``audio_payload``:必填,待转写音频(对齐 ``GenerationRequest.audio_payload``
          字段名,validate_against_schema 通过 ``_request_value`` 直接读到对应字段);
        - ``language``:可选,ISO 语言码;为空时上游自动识别;
        - ``include_timestamps``:可选,是否输出 segments;默认 True。
        """
        lang_spec: dict[str, object] = {"required": False, "description": "语言码(ISO 639-1,如 en/zh/ja),留空自动识别"}
        if self.supported_languages:
            lang_spec["values"] = list(self.supported_languages)
            lang_spec["description"] = f"语言码({', '.join(self.supported_languages)}),留空自动识别"

        schema: dict[str, PortSpec] = {
            "audio_payload": PortSpec(
                type=PortType.AUDIO,
                required=True,
                mime_types=list(self.audio_formats),
                description=f"待转写音频(≤{self.max_audio_bytes // (1024 * 1024)}MB / {self.max_audio_seconds}s)",
            ),
            "language": PortSpec(type=PortType.STRING, **lang_spec),  # type: ignore[arg-type]
            "include_timestamps": PortSpec(
                type=PortType.BOOLEAN,
                default=self.include_timestamps_default,
                required=False,
                description="是否返回带时间戳的分段(segments);关闭只返回全文文本",
            ),
            "params": PortSpec(
                type=PortType.STRING,
                required=False,
                description="预留扩展(后端私有参数透传,例如 ignore_timestamps 等)",
            ),
        }
        return schema

    def input_schema(self) -> dict[str, PortSpec]:
        return self.base_asr_schema()

    def output_schema(self) -> dict[str, PortSpec]:
        """ASR 默认输出文本主体 + 可选时间戳分段"""
        return {
            "text": PortSpec(type=PortType.OUTPUT_TEXT, description="转写出的全文文本(UTF-8)"),
            "segments": PortSpec(
                type=PortType.OUTPUT_TEXT,
                required=False,
                description="带时间戳的分段(JSON 序列化后的字符串,见 metadata.segments)",
            ),
        }

    # ── 跨字段校验 ──

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        # ASR 必须有音频输入;参考音频字段优先,其次 audio_refs[0]
        if not request.audio_payload and not request.audio_refs:
            raise ValidationError(f"{self.display_name} 需要提供音频输入(audio / reference_audio)")
        audio_bytes = request.audio_payload
        if audio_bytes is not None and len(audio_bytes) > self.max_audio_bytes:
            mb = len(audio_bytes) / (1024 * 1024)
            raise ValidationError(
                f"{self.display_name} 音频大小 {mb:.2f}MB 超过上限 {self.max_audio_bytes // (1024 * 1024)}MB"
            )
        # 语言码在白名单内时校验(空字符串/None 视为自动识别,放行)
        lang = request.language_boost or ""
        if lang and self.supported_languages and lang not in self.supported_languages:
            raise ValidationError(f"{self.display_name} 不支持语言 {lang!r},可选: {self.supported_languages}")


__all__ = ["AsrGenerationBase"]
