"""Gemini 3.8 Flash-Lite TTS:音色、计费日期、启用列表。"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.utils.core.types import CapabilityManifest
from RH_ComfyUI.models.speech.defs import GeminiTtsDef
from RH_ComfyUI.utils.core.request import OutputType, GenerationResult
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.core.pipeline import NodeDef
from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from RH_ComfyUI.utils.mappers.gemini_tts import gemini_tts_mapper
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsListStrConfig, GsRepeatGroupConfig
from RH_ComfyUI.utils.backends.gemini_speech.api import GeminiSpeechAPI, GeminiSpeechAudio, gemini_tts_wire_body
from RH_ComfyUI.utils.mappers.gemini_tts_billing import (
    gemini_tts_rates,
    settle_gemini_tts_points,
    estimate_gemini_tts_points,
)
from RH_ComfyUI.utils.backends.gemini_image.config import GEMINI_MODEL_OPTIONS
from RH_ComfyUI.utils.backends.gemini_speech.voices import GEMINI_TTS_VOICES, GEMINI_TTS_DEFAULT_VOICE
from RH_ComfyUI.utils.backends.gemini_speech.executor import GeminiSpeechAdapter


def test_gemini_tts_is_a_separate_checkbox():
    assert "gemini_tts" in GEMINI_MODEL_OPTIONS
    pool = SERVICE_CONFIG_DEFAULT["OpenAI_Image_Providers"]
    assert isinstance(pool, GsRepeatGroupConfig)
    models = pool.template["models"]
    assert isinstance(models, GsRepeatGroupConfig)
    real_name = models.template["model_real_name"]
    assert isinstance(real_name, GsStrConfig)
    names = real_name.options or []
    assert "gemini_tts" not in names
    assert "banana2.lite" in names
    item = SERVICE_CONFIG_DEFAULT["Gemini_Enabled_Models"]
    assert isinstance(item, GsListStrConfig)
    assert item.options[-1] == "gemini_tts"
    assert item.data == item.options


def test_schema_is_single_speaker_wav():
    model = GeminiTtsDef()
    schema = model.input_schema()
    assert model.max_text_length == 4000
    assert model.supports_voice_clone is False
    assert "reference_audio" not in schema
    assert "speed" not in schema
    assert schema["voice_id"].values == list(GEMINI_TTS_VOICES)
    assert schema["voice_id"].default == GEMINI_TTS_DEFAULT_VOICE
    assert len(GEMINI_TTS_VOICES) == 30
    lo, hi = model.point_range()
    assert lo < hi
    assert lo == estimate_gemini_tts_points("")
    assert hi == estimate_gemini_tts_points("你" * 4000)


def test_unknown_voice_rejected():
    model = GeminiTtsDef()
    request = GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", voice_id="NotAVoice")
    with pytest.raises(ValidationError):
        model.validate(request)


def test_wire_body_defaults_kore_and_skips_image_fields():
    empty = gemini_tts_wire_body(model="gemini-3.8-flash-lite-tts", text="你好", voice="", style="")
    assert empty["response_modalities"] == ["AUDIO"]
    assert empty["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Kore"
    assert "speech_metadata" not in empty
    assert "image_config" not in empty
    assert "thinking_config" not in empty
    styled = gemini_tts_wire_body(model="gemini-3.8-flash-lite-tts", text="你好", voice="Puck", style="平静")
    assert styled["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Puck"
    assert "speech_metadata" in styled
    assert styled["speech_metadata"] == {"style": "平静"}


def test_mapper_defaults_voice_and_keeps_style_off_transcript():
    class _Fake(GeminiSpeechAPI):
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        async def synthesize(self, *, model: str, text: str, voice: str, style: str) -> GeminiSpeechAudio:
            self.kwargs = {"model": model, "text": text, "voice": voice, "style": style}
            return GeminiSpeechAudio(
                audio=b"RIFF",
                usage={"usage_metadata": {"prompt_token_count": 2, "candidates_token_count": 25}},
            )

    api = _Fake()
    request = GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", mood="轻快")
    result = asyncio.run(gemini_tts_mapper(request, api, model="gemini-3.8-flash-lite-tts"))
    assert api.kwargs == {
        "model": "gemini-3.8-flash-lite-tts",
        "text": "你好",
        "voice": "Kore",
        "style": "轻快",
    }
    assert result.mime_type == "audio/wav"
    assert result.data == b"RIFF"
    meta = result.usage["usage_metadata"]
    assert isinstance(meta, dict)
    assert meta["candidates_token_count"] == 25


def test_executor_keeps_usage_metadata():
    async def _mapper(request: GenerationRequest, api: object) -> GenerationResult:
        _ = (request, api)
        result = GenerationResult(output_type=OutputType.AUDIO, data=b"RIFF", mime_type="audio/wav")
        result.usage["usage_metadata"] = {"prompt_token_count": 3, "candidates_token_count": 25}
        return result

    node = NodeDef(
        name="gemini_tts",
        display_name="Gemini 3.8 Flash-Lite TTS",
        task_type=TaskType.SPEECH,
        backend="gemini_speech",
        point_cost=1,
        mapper_func=_mapper,
        capabilities=CapabilityManifest(supported_tasks=["speech"], mode="sync"),
    )
    out = asyncio.run(
        GeminiSpeechAdapter().execute(
            GenerationRequest(task_type=TaskType.SPEECH, prompt="你好"),
            node,
        )
    )
    assert out.mime_type == "audio/wav"
    meta = out.usage["usage_metadata"]
    assert isinstance(meta, dict)
    assert meta["candidates_token_count"] == 25
    assert out.usage["points"] == 0


def test_rates_step_on_2027():
    assert gemini_tts_rates(date(2026, 12, 31)) == (50, 600)
    assert gemini_tts_rates(date(2027, 1, 1)) == (100, 1200)


def test_short_text_is_one_point():
    when = date(2026, 10, 16)
    assert estimate_gemini_tts_points("", today=when) == 1
    assert estimate_gemini_tts_points("你好", today=when) == 1
    assert estimate_gemini_tts_points("", today=date(2027, 1, 1)) == 1


def test_four_thousand_chars_follow_the_dated_cap():
    text = "你" * 4000
    assert estimate_gemini_tts_points(text, today=date(2026, 12, 31)) == 11
    assert estimate_gemini_tts_points(text, today=date(2027, 1, 1)) == 21


def test_settle_uses_audio_detail_then_candidate_count():
    when = date(2026, 12, 31)
    detailed = settle_gemini_tts_points(
        {
            "usage_metadata": {
                "prompt_token_count": 10,
                "candidates_token_count": 999,
                "candidates_tokens_details": [{"modality": "AUDIO", "token_count": 25}],
            }
        },
        today=when,
    )
    # 10*50 + 25*600 = 15500 → 1
    assert detailed == 1
    plain = settle_gemini_tts_points(
        {"usage_metadata": {"prompt_token_count": 4000, "candidates_token_count": 16384}},
        today=when,
    )
    assert plain == 11
    assert (
        settle_gemini_tts_points(
            {"usage_metadata": {"prompt_token_count": 0, "candidates_token_count": 0}},
            today=when,
        )
        is None
    )
    assert settle_gemini_tts_points({}, today=when) is None


def test_old_gemini_list_does_not_enable_tts(monkeypatch):
    import RH_ComfyUI.utils.backends.gemini_image.config as gcfg

    saved = ["banana1", "banana2", "banana2.1", "banana2.lite", "banana_pro"]

    def _cfg(key: str) -> object:
        if key == "Gemini_Enable":
            return True
        if key == "Gemini_Enabled_Models":
            return saved
        return None

    monkeypatch.setattr(gcfg, "_cfg", _cfg)
    assert gcfg.is_gemini_model_enabled("banana2") is True
    assert gcfg.is_gemini_model_enabled("gemini_tts") is False
    assert asyncio.run(GeminiTtsDef().check_available()) is False
    reason = asyncio.run(GeminiTtsDef().unavailable_reason())
    assert "gemini_tts" in reason
    gate = AdapterChannel("gemini_speech", model_name="gemini_tts")._enable_gate()
    assert gate is not None
    assert "gemini_tts" in gate


def test_empty_gemini_list_closes_tts(monkeypatch):
    import RH_ComfyUI.utils.backends.gemini_image.config as gcfg

    monkeypatch.setattr(gcfg, "_cfg", lambda key: [] if key == "Gemini_Enabled_Models" else True)
    assert gcfg.is_gemini_model_enabled("gemini_tts") is False
    assert asyncio.run(GeminiTtsDef().check_available()) is False
