"""Fish Audio ASR 映射函数 — 调 /v1/asr,把结果回填到 NodeOutput

约定(与 TTS mapper 对齐):
- request.audio_payload 优先;为 None 时回退 audio_refs[0].data;
- language 通过 request.language_boost(沿用 GenerationRequest 已有字段)透传;
- include_timestamps 通过 request.params['include_timestamps'] 透传,默认 True;
- 失败抛 RuntimeError,由 executor → ChannelError 上抛,让上层 run() 走切换通道逻辑。
"""

from __future__ import annotations

import json
from typing import Any

from gsuid_core.logger import logger

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.fishaudio.api import FishAudioAPI


def _resolve_audio(request: GenerationRequest) -> bytes | None:
    """按 audio_payload → audio_refs[0].data 的优先级解析 ASR 输入"""
    if request.audio_payload:
        return request.audio_payload
    if request.audio_refs:
        ref = request.audio_refs[0]
        if ref.data:
            return ref.data
        if ref.url:
            # 上游一般给的是本地 /api/canvas-backend/... 路径,canvas_backend 层
            # 已经解过;此处不主动下载(避免重复 IO 与未鉴权外链),仅记日志
            logger.warning("[FishAudio ASR] audio_refs[0] 只有 url 未携带 bytes,跳过")
    return None


async def fishaudio_asr_mapper(
    request: GenerationRequest,
    api: FishAudioAPI,
) -> GenerationResult:
    """Fish Audio ASR:取音频 → POST /v1/asr → 回填文本/分段/时长"""
    audio = _resolve_audio(request)
    if not audio:
        raise RuntimeError("Fish Audio ASR 缺少音频输入(audio_payload / audio_refs[0].data)")

    language = (request.language_boost or "").strip() or None
    include_ts = bool(request.params.get("include_timestamps", True))
    ignore_timestamps = not include_ts

    raw = await api.asr(audio, language=language, ignore_timestamps=ignore_timestamps)
    if isinstance(raw, str):
        # FishAudioAPI 失败语义:返回人话错误信息(str)
        raise RuntimeError(f"Fish Audio 语音识别失败: {raw}")

    text: str = raw.get("text") or ""
    segments: list[dict[str, Any]] = raw.get("segments") or []
    duration: float = float(raw.get("duration") or 0.0)

    if not text:
        # 上游没拒绝但也没识别出文本(可能是空白音频 / 纯音乐),按人话回报
        raise RuntimeError("Fish Audio 语音识别未返回文本(音频可能没有可识别的语音)")

    # 主产物 = 全文文本(UTF-8 字节)
    text_bytes = text.encode("utf-8")

    # 附属产物:segments 序列化成 JSON 字符串便于跨端口传输;duration 放 metadata
    extras: dict[str, Any] = {}
    if include_ts and segments:
        extras["segments"] = json.dumps(segments, ensure_ascii=False).encode("utf-8")
    extras["duration"] = str(duration).encode("utf-8")  # NodeOutput.data 期望 bytes

    return GenerationResult(
        output_type=OutputType.TEXT,
        data=text_bytes,
        mime_type="text/plain; charset=utf-8",
        outputs=extras,
        raw={"text": text, "duration": duration, "segments": segments},
        metadata={"duration_seconds": duration, "segments_count": len(segments)},
    )
