"""Gemini 3.8 Flash-Lite TTS。

生图的 generate() 写死 response_modalities=IMAGE。语音必须另开一次
generate_content,只发 AUDIO 和 speech_config,不能带 image_config。
"""

from __future__ import annotations

from typing import TypedDict
from dataclasses import dataclass

from gsuid_core.logger import logger

from .voices import GEMINI_TTS_DEFAULT_VOICE
from ..gemini_image.api import GeminiImageAPI


class _PrebuiltVoiceWire(TypedDict):
    voice_name: str


class _VoiceConfigWire(TypedDict):
    prebuilt_voice_config: _PrebuiltVoiceWire


class _SpeechConfigWire(TypedDict):
    voice_config: _VoiceConfigWire


class _SpeechMetadataWire(TypedDict):
    style: str


class _GeminiTtsWireBase(TypedDict):
    model: str
    text: str
    response_modalities: list[str]
    speech_config: _SpeechConfigWire


class GeminiTtsWire(_GeminiTtsWireBase, total=False):
    speech_metadata: _SpeechMetadataWire


@dataclass(frozen=True)
class GeminiSpeechAudio:
    audio: bytes
    usage: dict[str, object]


def gemini_tts_wire_body(*, model: str, text: str, voice: str, style: str) -> GeminiTtsWire:
    """统计用请求体。不含密钥,也不含生图字段。"""
    voice_name = voice.strip() or GEMINI_TTS_DEFAULT_VOICE
    body: GeminiTtsWire = {
        "model": model,
        "text": text,
        "response_modalities": ["AUDIO"],
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": voice_name}}},
    }
    style_text = style.strip()
    if style_text:
        body["speech_metadata"] = {"style": style_text}
    return body


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _vendor_payload(response: object) -> dict[str, object]:
    from google.genai.types import GenerateContentResponse

    if not isinstance(response, GenerateContentResponse):
        return {}
    dumped = response.model_dump(mode="json", exclude_none=True)
    blob = _as_dict(dumped)
    if blob is None:
        return {}
    copied: dict[str, object] = {}
    for key, item in blob.items():
        if key != "sdk_http_response":
            copied[key] = item
    return copied


def _usage_blob(response: object) -> dict[str, object]:
    payload = _vendor_payload(response)
    if "usage_metadata" not in payload:
        return {}
    meta = _as_dict(payload["usage_metadata"])
    if not meta:
        return {}
    return {"usage_metadata": meta}


def _first_audio(response: object) -> bytes:
    from google.genai.types import GenerateContentResponse

    if not isinstance(response, GenerateContentResponse):
        raise RuntimeError("Gemini 语音响应类型异常")
    candidates = response.candidates or []
    if not candidates:
        raise RuntimeError("Gemini 语音响应没有音频")
    content = candidates[0].content
    parts = list(content.parts) if content is not None and content.parts else []
    for part in parts:
        inline = part.inline_data
        if inline is None or not inline.data:
            continue
        data = inline.data
        if isinstance(data, bytes):
            return data
        if isinstance(data, str) and data:
            import base64

            return base64.b64decode(data)
    raise RuntimeError("Gemini 语音响应没有音频")


class GeminiSpeechAPI:
    """凭证热读 GeminiImageAPI,不在这里冻 key。"""

    def is_configured(self) -> bool:
        return GeminiImageAPI().is_configured()

    async def synthesize(self, *, model: str, text: str, voice: str, style: str) -> GeminiSpeechAudio:
        from google.genai import types

        from ....core.telemetry.wire_capture import set_vendor_raw, set_wire_from_http_body

        voice_name = voice.strip() or GEMINI_TTS_DEFAULT_VOICE
        style_text = style.strip()
        set_wire_from_http_body(
            gemini_tts_wire_body(model=model, text=text, voice=voice_name, style=style_text),
            prompt=text,
        )
        part = types.Part(text=text)
        if style_text:
            # Part.__init__ 没把 speech_metadata 列进显式参数,字段本身是有的。
            part.speech_metadata = types.SpeechMetadata(style=style_text)
        contents = types.Content(role="user", parts=[part])
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name),
                ),
            ),
        )
        client = GeminiImageAPI()._build_client()
        logger.info(
            f"[Gemini-Speech] generate_content model={model} voice={voice_name} style={'yes' if style_text else 'no'}"
        )
        response: object = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        set_vendor_raw(_vendor_payload(response))
        return GeminiSpeechAudio(audio=_first_audio(response), usage=_usage_blob(response))


__all__ = [
    "GeminiSpeechAPI",
    "GeminiSpeechAudio",
    "GeminiTtsWire",
    "gemini_tts_wire_body",
]
