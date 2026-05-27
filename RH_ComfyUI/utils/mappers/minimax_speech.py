"""MiniMax 语音合成映射函数"""

from __future__ import annotations

import hashlib

from gsuid_core.logger import logger

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.minimax.api import MiniMaxAPI


async def minimax_t2a_speech_mapper(
    request: GenerationRequest,
    api: MiniMaxAPI,
) -> GenerationResult:
    """MiniMax T2A 异步语音合成映射+执行

    使用 MiniMax /v1/t2a_async_v2 接口进行语音合成，支持：
    - 预置音色语音合成
    - 音色克隆（通过参考音频上传+复刻获取 voice_id）
    - 情绪控制、语速调节

    字段映射：
    - request.prompt → 待合成文本
    - request.mood → emotion 情绪参数
    - request.reference_audio → 音色克隆参考音频
    - request.extra.voice_id → 自定义音色 ID
    """
    voice_id: str = "audiobook_male_1"

    # 如果有参考音频，先上传并克隆音色
    if request.reference_audio is not None:
        cloned_voice_id = await _clone_voice_from_audio(api, request.reference_audio)
        if cloned_voice_id:
            voice_id = cloned_voice_id
            logger.info(f"[MiniMax] 使用克隆音色: {voice_id}")
        else:
            raise RuntimeError(
                "MiniMax 音色复刻失败，已停止生成，避免回退为默认男声。请检查账号是否开通 voice_clone 权限、参考音频是否合规。"
            )
    elif request.extra and request.extra.get("voice_id"):
        voice_id = request.extra["voice_id"]

    speed = float(request.extra.get("speed", 1.0)) if request.extra else 1.0
    model = request.extra.get("model", "speech-2.8-hd") if request.extra else "speech-2.8-hd"
    language_boost = request.extra.get("language_boost", "auto") if request.extra else "auto"

    audio_bytes = await api.generate_speech(
        text=request.prompt,
        voice_id=voice_id,
        speed=speed,
        emotion=request.mood,
        model=model,
        language_boost=language_boost,
    )

    if isinstance(audio_bytes, int):
        raise RuntimeError(f"MiniMax T2A 语音合成失败，错误码: {audio_bytes}")

    if not audio_bytes:
        raise RuntimeError("MiniMax T2A 语音合成失败，未返回音频数据")

    return GenerationResult(
        output_type=OutputType.AUDIO,
        data=audio_bytes,
        mime_type="audio/mpeg",
    )


async def _clone_voice_from_audio(api: MiniMaxAPI, audio_data: bytes) -> str | None:
    """将参考音频上传到 MiniMax 并克隆音色

    Args:
        api: MiniMaxAPI 实例
        audio_data: 参考音频字节数据

    Returns:
        克隆成功返回 voice_id，失败返回 None
    """
    # 生成唯一的 voice_id（基于音频内容哈希）
    audio_hash = hashlib.md5(audio_data).hexdigest()[:16]
    voice_id = f"RH{audio_hash}"

    # 检测音频格式
    filename = "audio.mp3"
    content_type = "audio/mpeg"
    if audio_data[:4] == b"RIFF":
        filename = "audio.wav"
        content_type = "audio/wav"

    # 1. 上传音频文件
    file_id = await api.upload_file(
        file_data=audio_data,
        purpose="voice_clone",
        filename=filename,
        content_type=content_type,
    )

    if isinstance(file_id, int) and file_id > 0:
        # 2. 克隆音色
        result = await api.clone_voice(
            file_id=file_id,
            voice_id=voice_id,
        )

        if result is True:
            return voice_id

        # 2038 "voice clone user forbidden" 在用户已开通权限的前提下，
        # 大概率是该 voice_id 已被克隆过，尝试复用而非直接失败。
        if result == 2038:
            logger.warning(f"[MiniMax] 音色克隆返回 2038，假设 voice_id 已存在，尝试复用: {voice_id}")
            return voice_id

        logger.warning(f"[MiniMax] 音色克隆失败: {result}")

    return None
