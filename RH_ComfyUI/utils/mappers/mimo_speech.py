"""XiaoMi MiMo TTS 语音合成映射函数"""

from __future__ import annotations

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.mimo.api import MIMOAPI


async def mimo_tts_mapper(
    request: GenerationRequest,
    api: MIMOAPI,
) -> GenerationResult:
    """MiMo TTS 语音合成映射+执行

    使用 MiMo-V2.5-TTS 系列接口进行语音合成。
    自动根据是否有参考音频选择模型：
    - 有 reference_audio → mimo-v2.5-tts-voiceclone（音色复刻）
    - 无 reference_audio → mimo-v2.5-tts（预置音色）

    字段映射：
    - request.prompt → assistant 消息（待合成文本/口播内容）
    - request.mood → user 消息（情绪/风格控制指令）
    - request.reference_audio → 音色复刻参考音频
    """
    audio_bytes = await api.generate_speech(
        text=request.prompt,
        mood=request.mood,
        reference_audio=request.reference_audio,
        model=request.params.get("model"),
        voice=request.voice_id or request.params.get("voice"),
    )

    if isinstance(audio_bytes, int):
        raise RuntimeError(f"MiMo TTS 语音合成失败，错误码: {audio_bytes}")

    if not audio_bytes:
        raise RuntimeError("MiMo TTS 语音合成失败，未返回音频数据")

    return GenerationResult(
        output_type=OutputType.AUDIO,
        data=audio_bytes,
        mime_type="audio/wav",
    )
