"""Gemini 3.8 Flash-Lite TTS 映射。情绪已在基类里从正文剥离,这里只读 mood。"""

from __future__ import annotations

from collections.abc import Callable, Awaitable

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.gemini_speech.api import GeminiSpeechAPI
from ..backends.gemini_speech.voices import GEMINI_TTS_VOICES, GEMINI_TTS_DEFAULT_VOICE


def _voice_name(raw: str | None) -> str:
    if isinstance(raw, str):
        name = raw.strip()
        if name in GEMINI_TTS_VOICES:
            return name
    return GEMINI_TTS_DEFAULT_VOICE


def _style(raw: str | None) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


async def gemini_tts_mapper(
    request: GenerationRequest,
    api: GeminiSpeechAPI,
    *,
    model: str,
) -> GenerationResult:
    spoken = await api.synthesize(
        model=model,
        text=request.prompt,
        voice=_voice_name(request.voice_id),
        style=_style(request.mood),
    )
    result = GenerationResult(output_type=OutputType.AUDIO, data=spoken.audio, mime_type="audio/wav")
    for key, value in spoken.usage.items():
        result.usage[key] = value
    return result


def gemini_tts_mapper_for(
    model: str,
) -> Callable[[GenerationRequest, GeminiSpeechAPI], Awaitable[GenerationResult]]:
    async def _mapper(request: GenerationRequest, api: GeminiSpeechAPI) -> GenerationResult:
        return await gemini_tts_mapper(request, api, model=model)

    return _mapper


__all__ = ["gemini_tts_mapper", "gemini_tts_mapper_for"]
