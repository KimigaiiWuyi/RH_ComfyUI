"""Fish Audio S2 TTS 映射函数 — 自动音色克隆(持久去重)+ 内联情绪合成

情绪已由语音基类 normalize() 内联进 request.prompt(见 core/base/emotion.py),
本 mapper 只负责:按参考音频复用/克隆音色 → 合成。
"""

from __future__ import annotations

import hashlib

from gsuid_core.logger import logger

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..database.models import RHVoiceCloneCache
from ..backends.fishaudio.api import FishAudioAPI

_PROVIDER = "fishaudio"


async def fishaudio_tts_mapper(
    request: GenerationRequest,
    api: FishAudioAPI,
) -> GenerationResult:
    """Fish Audio 合成:参考音频→复用/克隆音色 id,再走 TTS"""
    reference_id: str | None = None
    if request.reference_audio is not None:
        reference_id = await _get_or_create_voice(api, request.reference_audio, request.user_id or "")
        if not reference_id:
            raise RuntimeError(
                "Fish Audio 音色克隆失败,已停止生成以避免回退随机音色;"
                "请检查 API Key 与参考音频是否合规。"
            )

    model = request.params.get("model")
    audio = await api.tts(
        text=request.prompt,
        reference_id=reference_id,
        model=model,
        speed=request.speed,
    )

    if isinstance(audio, str):
        raise RuntimeError(f"Fish Audio 语音合成失败: {audio}")
    if not audio:
        raise RuntimeError("Fish Audio 语音合成失败,未返回音频数据")

    return GenerationResult(output_type=OutputType.AUDIO, data=audio, mime_type="audio/mpeg")


async def _get_or_create_voice(api: FishAudioAPI, audio: bytes, created_by: str) -> str | None:
    """按参考音频内容哈希复用已克隆音色;未命中则克隆一次并持久记住"""
    audio_hash = hashlib.sha256(audio).hexdigest()

    cached = await RHVoiceCloneCache.get_voice_id(_PROVIDER, audio_hash)
    if cached:
        logger.info(f"[FishAudio] 命中音色缓存,复用: {cached}")
        return cached

    title = f"RH-{audio_hash[:12]}"
    voice_id = await api.create_voice_model(audio, title)
    if not voice_id:
        return None

    await RHVoiceCloneCache.remember(
        provider=_PROVIDER,
        audio_hash=audio_hash,
        voice_model_id=voice_id,
        title=title,
        created_by=created_by,
    )
    return voice_id
