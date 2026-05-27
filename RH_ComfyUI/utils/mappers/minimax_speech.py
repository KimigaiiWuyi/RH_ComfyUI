"""MiniMax 语音合成映射函数"""

from __future__ import annotations

import time
import random
import hashlib

from gsuid_core.logger import logger

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.minimax.api import MiniMaxAPI

# 内存缓存：audio_hash -> voice_id
# 用于避免对同一段参考音频反复调用 MiniMax voice_clone 接口（有频率/额度限制）
_voice_cache: dict[str, str] = {}


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

    # 如果 T2A 返回 2013 (voice id wrong)，说明缓存的 voice_id 已过期或被清理
    # 清除缓存后强制重新克隆（使用新 voice_id）并重试一次
    if audio_bytes == 2013 and request.reference_audio is not None:
        logger.warning("[MiniMax] T2A 返回 2013 (voice id wrong)，voice_id 可能已失效，尝试重新克隆")
        audio_hash = hashlib.md5(request.reference_audio).hexdigest()[:16]
        _voice_cache.pop(audio_hash, None)

        cloned_voice_id = await _clone_voice_from_audio(api, request.reference_audio, force_new=True)
        if cloned_voice_id:
            voice_id = cloned_voice_id
            logger.info(f"[MiniMax] 重新克隆成功，使用新音色: {voice_id}")
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


async def _clone_voice_from_audio(
    api: MiniMaxAPI,
    audio_data: bytes,
    force_new: bool = False,
) -> str | None:
    """将参考音频上传到 MiniMax 并克隆音色

    使用本地内存缓存避免对同一段音频反复调用 clone_voice 接口。
    当 force_new=True 时，生成带随机后缀的 voice_id 以绕过可能的 ID 冲突。

    Args:
        api: MiniMaxAPI 实例
        audio_data: 参考音频字节数据
        force_new: 是否强制生成新的 voice_id（用于缓存失效后的重试）

    Returns:
        克隆成功返回 voice_id，失败返回 None
    """
    audio_hash = hashlib.md5(audio_data).hexdigest()[:16]

    # 1. 检查本地缓存
    if not force_new and audio_hash in _voice_cache:
        cached_voice_id = _voice_cache[audio_hash]
        logger.info(f"[MiniMax] 音色缓存命中，跳过 clone_voice，直接复用: {cached_voice_id}")
        return cached_voice_id

    # 2. 生成 voice_id
    if force_new:
        # 强制新克隆：加时间戳+随机数，确保不与其他 ID 冲突
        voice_id = f"RH{audio_hash}_{int(time.time())}_{random.randint(1000, 9999)}"
    else:
        voice_id = f"RH{audio_hash}"

    # 3. 检测音频格式
    filename = "audio.mp3"
    content_type = "audio/mpeg"
    if audio_data[:4] == b"RIFF":
        filename = "audio.wav"
        content_type = "audio/wav"

    # 4. 上传音频文件
    file_id = await api.upload_file(
        file_data=audio_data,
        purpose="voice_clone",
        filename=filename,
        content_type=content_type,
    )

    if not (isinstance(file_id, int) and file_id > 0):
        logger.warning(f"[MiniMax] 文件上传失败: {file_id}")
        return None

    # 5. 克隆音色
    result = await api.clone_voice(
        file_id=file_id,
        voice_id=voice_id,
    )

    if result is True:
        logger.info(f"[MiniMax] 音色克隆成功，写入缓存: {voice_id}")
        _voice_cache[audio_hash] = voice_id
        return voice_id

    # 2038 "voice clone user forbidden" —— 大概率是频率/额度限制，而非权限未开通
    # 此时 voice_id 并未被创建，绝不能复用，直接返回 None 让上层处理
    if result == 2038:
        logger.warning(
            "[MiniMax] 音色克隆返回 2038 (voice clone user forbidden)，"
            "可能是频率/额度限制，voice_id 未被创建。请稍后重试。"
        )
        return None

    logger.warning(f"[MiniMax] 音色克隆失败: {result}")
    return None
