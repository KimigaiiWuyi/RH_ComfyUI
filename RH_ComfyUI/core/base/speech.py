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

import re

from .errors import ValidationError
from .emotion import (
    EmotionStyle,
    to_inline_tag,
    to_enum_emotion,
    render_inline_markers,
    extract_emotion_markers,
    strip_written_letter_fillers,
)
from .generation import AIGCGenerationBase
from ..schema.types import MediaRef, PortSpec, PortType
from ..schema.request import TaskType, GenerationRequest

# 画布把 @ 音频收成这个代号，序号与 audio_refs 下标对齐、从 1 起。
# 与 canvas_backend voice_refs 同一套正则：计费前改一次，这里在进 mapper 前再兜底。
_AUDIO_MENTION_RE = re.compile(r"\[@参考音频(\d+)\]")
_SPK_PLACEHOLDER_RE = re.compile(r"<<SPK:(\d+)>>")
_SPEAKER_TAG_RE = re.compile(r"<\|speaker:(\d+)\|>")
_INLINE_SPACING_RE = re.compile(r"[ \t]{2,}")
_INLINE_EOL_RE = re.compile(r"[ \t]+\n")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _collapse_inline_spacing(text: str) -> str:
    """折叠内联空白，保留换行。与 voice_refs / 前端 stripMentions 的收尾一致。"""
    out = _INLINE_SPACING_RE.sub(" ", text)
    out = _INLINE_EOL_RE.sub("\n", out)
    return _BLANK_RUN_RE.sub("\n\n", out).strip()


def _audio_ref_bytes(ref: MediaRef) -> bytes | None:
    """只收已经解码的字节。裸 URL 不在归一化里下载，避免把「没内联」误当成默认音色。"""
    data = ref.data
    if isinstance(data, bytes) and data:
        return data
    return None


def _first_clip(aligned: list[bytes | None]) -> bytes | None:
    for clip in aligned:
        if isinstance(clip, bytes) and clip:
            return clip
    return None


def _strip_voice_placeholders(prompt: str) -> str:
    """单音色不能把代号留在正文里：Fish 会把标记当台词念出来。"""
    text = _AUDIO_MENTION_RE.sub("", prompt or "")
    text = _SPK_PLACEHOLDER_RE.sub("", text)
    return _collapse_inline_spacing(text)


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
        """先把连线参考音提升成克隆端口，再做情绪整形。

        mapper 只读 reference_audio / reference_audios，不读 audio_refs。
        情绪展开会改写正文，说话人代号必须在那之前落成 <|speaker:N|>。
        """
        request = super().normalize(request)
        request = self._adopt_linked_voice(request)
        request = self._apply_emotion(request)
        request.prompt = strip_written_letter_fillers(request.prompt)
        return request

    def _adopt_linked_voice(self, request: GenerationRequest) -> GenerationRequest:
        """audio_refs 里已有字节、但克隆端口还空着时，补上音色。

        画布在计费前按 schema 做过同样的改写。schema 查不到时那一步会静默跳过，
        任务仍成功，只是走了内置默认音色。这里兜住所有入口。
        调用方已经写了 reference_audio / reference_audios 时不动，避免盖掉显式音色。
        不清理 audio_refs：ASR 和多模态视频还要读它。
        """
        if not self.supports_voice_clone:
            return request
        if request.reference_audio is not None or request.reference_audios:
            return request

        aligned = [_audio_ref_bytes(ref) for ref in request.audio_refs]
        first = _first_clip(aligned)
        if first is None:
            return request

        if "reference_audios" in self.input_schema():
            _adopt_fish_speakers(request, aligned)
        else:
            request.reference_audio = first
            request.prompt = _strip_voice_placeholders(request.prompt)
        return request

    def _apply_emotion(self, request: GenerationRequest) -> GenerationRequest:
        """按 emotion_style 把(prompt, 情绪块, mood)整形成上游能直接消费的形态

        情绪只来自显式情绪块 `<<EMO: label>>` 与结构化 mood;正文里字面 `[..]`/`【..】`
        一律当普通文本(不翻译、不剥离)。

        - inline_bracket:情绪块就地展开为 `[english]` + 结构化情绪并入句首,mood 清空
        - enum:剥离情绪块 + 结构化情绪/块标签收敛到枚举
        - natural_language:剥离情绪块;无结构化情绪时用首个块标签兜底
        - none / 不支持情绪:剥离情绪块并清空 mood
        """
        style = self.emotion_style if self.supports_mood else EmotionStyle.NONE

        if style is EmotionStyle.INLINE_BRACKET:
            text = render_inline_markers(request.prompt)
            if request.mood:
                text = f"{to_inline_tag(request.mood)} {text}"
            request.prompt = text
            request.mood = None
            return request

        cleaned, labels = extract_emotion_markers(request.prompt)
        request.prompt = cleaned

        if style is EmotionStyle.ENUM:
            request.mood = to_enum_emotion(request.mood, labels, self.emotion_enum)
        elif style is EmotionStyle.NATURAL_LANGUAGE:
            # 句中标记不是这些模型的音频标签,只从正文抹掉,不转成整句 mood
            pass
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
            raise ValidationError(f"{self.display_name} 不支持参考音频克隆;请换用支持克隆的模型")
        if (
            request.reference_audio is None
            and self.supports_voice_clone
            and not self.builtin_voices
            and not self.has_default_voice
        ):
            # 无预置音色且未提供参考音频的模型,必须给出明确指引而非产出随机音色
            raise ValidationError(f"{self.display_name} 需要提供参考音频(reference_audio)以确定音色")


def _adopt_fish_speakers(request: GenerationRequest, aligned: list[bytes | None]) -> None:
    """Fish：两条以上 [@参考音频N] 才改写成 <|speaker:N|> 并填 reference_audios。

    只有一名时不留说话人标记。正文里已经是 <|speaker:N|>、且没有中文代号时，
    按序号把 audio_refs 填进 reference_audios，不改标记本身。
    序号越界的代号删掉，不造一个没有音频的说话人。
    """
    speakers: list[bytes] = []
    index_by_mention: dict[int, int] = {}

    def _repl(match: re.Match[str]) -> str:
        idx = int(match.group(1)) - 1
        data = aligned[idx] if 0 <= idx < len(aligned) else None
        if not isinstance(data, bytes):
            return ""
        if idx not in index_by_mention:
            index_by_mention[idx] = len(speakers)
            speakers.append(data)
        return f"<<SPK:{index_by_mention[idx]}>>"

    original = request.prompt
    prompt = _AUDIO_MENTION_RE.sub(_repl, original)

    if len(speakers) >= 2:
        prompt = _SPK_PLACEHOLDER_RE.sub(r"<|speaker:\1|>", prompt)
        request.reference_audios = speakers
        request.reference_audio = speakers[0]
        request.prompt = _collapse_inline_spacing(prompt)
        return

    if not speakers and not _AUDIO_MENTION_RE.search(original):
        seen: set[int] = set()
        ordered: list[int] = []
        for match in _SPEAKER_TAG_RE.finditer(prompt):
            index = int(match.group(1))
            if index in seen:
                continue
            seen.add(index)
            ordered.append(index)
        in_range = [index for index in ordered if 0 <= index < len(aligned) and isinstance(aligned[index], bytes)]
        if len(in_range) >= 2:
            clips = _contiguous_clips(aligned, max(in_range))
            if clips is not None:
                request.reference_audios = clips
                request.reference_audio = clips[0]
                request.prompt = _collapse_inline_spacing(prompt)
                return

    prompt = _SPK_PLACEHOLDER_RE.sub("", prompt)
    chosen = speakers[0] if speakers else _first_clip(aligned)
    if not isinstance(chosen, bytes):
        return
    request.reference_audio = chosen
    request.prompt = _collapse_inline_spacing(prompt)


def _contiguous_clips(aligned: list[bytes | None], last: int) -> list[bytes] | None:
    """<|speaker:N|> 的 N 就是列表下标。中间缺一条字节就对不齐，宁可退回单音色。"""
    clips: list[bytes] = []
    for clip in aligned[: last + 1]:
        if not isinstance(clip, bytes) or not clip:
            return None
        clips.append(clip)
    if len(clips) < 2:
        return None
    return clips


__all__ = ["DigitalHumanSpeechBase"]
