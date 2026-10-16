"""Qwen-Audio TTS 映射。mood 作为 instruction,不写进正文。"""

from __future__ import annotations

from collections.abc import Callable, Awaitable

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.dashscope.speech_api import DashScopeSpeechAPI


def _voice_name(raw: str | None, voices: tuple[str, ...], default: str) -> str:
    if isinstance(raw, str):
        name = raw.strip()
        if name in voices:
            return name
    return default


def _instruction(raw: str | None) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


async def qwen_tts_mapper(
    request: GenerationRequest,
    api: DashScopeSpeechAPI,
    *,
    model: str,
    voices: tuple[str, ...],
    default_voice: str,
) -> GenerationResult:
    spoken = await api.synthesize(
        model=model,
        text=request.prompt,
        voice=_voice_name(request.voice_id, voices, default_voice),
        rate=request.speed,
        instruction=_instruction(request.mood),
    )
    result = GenerationResult(output_type=OutputType.AUDIO, data=spoken.audio, mime_type="audio/mpeg")
    for key, value in spoken.usage.items():
        result.usage[key] = value
    return result


def qwen_tts_mapper_for(
    model: str,
    voices: tuple[str, ...],
    default_voice: str,
) -> Callable[[GenerationRequest, DashScopeSpeechAPI], Awaitable[GenerationResult]]:
    async def _mapper(request: GenerationRequest, api: DashScopeSpeechAPI) -> GenerationResult:
        return await qwen_tts_mapper(
            request,
            api,
            model=model,
            voices=voices,
            default_voice=default_voice,
        )

    return _mapper


__all__ = ["qwen_tts_mapper", "qwen_tts_mapper_for"]
