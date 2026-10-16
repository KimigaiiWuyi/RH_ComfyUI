"""Qwen-Audio TTS:分模型音色、字符/token 计费、WebSocket 帧序。"""

from __future__ import annotations

import json
import asyncio

import pytest

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.models.speech.defs import _QWEN_TTS_BY_NAME
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsListStrConfig
from RH_ComfyUI.utils.mappers.qwen_tts_voices import (
    QWEN_TTS_31,
    QWEN_TTS_PLUS,
    QWEN_31_VOICES,
    QWEN_TTS_FLASH,
    QWEN_TTS_MODELS,
    QWEN_PLUS_VOICES,
    QWEN_FLASH_VOICES,
)
from RH_ComfyUI.utils.mappers.qwen_tts_billing import (
    qwen_tts_region,
    qwen_weighted_chars,
    settle_qwen_tts_points,
    estimate_qwen_tts_points,
)
from RH_ComfyUI.utils.backends.dashscope.config import DASHSCOPE_MODEL_OPTIONS
from RH_ComfyUI.utils.backends.dashscope.speech_api import (
    DashScopeSpeechAPI,
    qwen_tts_wire_body,
    dashscope_tts_ws_url,
)

_BEIJING = "https://dashscope.aliyuncs.com/api/v1"
_INTL = "https://dashscope-intl.aliyuncs.com/api/v1"


def test_dashscope_options_include_three_tts_names():
    assert QWEN_TTS_MODELS == DASHSCOPE_MODEL_OPTIONS[2:]
    item = SERVICE_CONFIG_DEFAULT["DashScope_Enabled_Models"]
    assert isinstance(item, GsListStrConfig)
    assert item.options == list(DASHSCOPE_MODEL_OPTIONS)
    assert item.data == item.options
    assert "qwen3-tts-flash-realtime" not in item.options


def test_system_voices_are_not_shared():
    assert len(QWEN_FLASH_VOICES) == 12
    assert len(QWEN_PLUS_VOICES) == 2
    assert len(QWEN_31_VOICES) == 68
    assert set(QWEN_FLASH_VOICES).isdisjoint(QWEN_PLUS_VOICES)
    assert set(QWEN_FLASH_VOICES).isdisjoint(QWEN_31_VOICES)
    assert set(QWEN_PLUS_VOICES).isdisjoint(QWEN_31_VOICES)
    flash = _QWEN_TTS_BY_NAME[QWEN_TTS_FLASH]()
    plus = _QWEN_TTS_BY_NAME[QWEN_TTS_PLUS]()
    latest = _QWEN_TTS_BY_NAME[QWEN_TTS_31]()
    assert flash.input_schema()["voice_id"].default == "longanhuan_v3.6"
    assert plus.input_schema()["voice_id"].default == "longanlingxin"
    assert latest.input_schema()["voice_id"].default == "longanhuan_v3.1"
    assert flash.max_text_length == 20000
    assert flash.supports_voice_clone is False
    assert "reference_audio" not in flash.input_schema()
    for model in (flash, plus, latest):
        lo, hi = model.point_range()
        assert lo < hi


def test_unknown_voice_and_foreign_voice_rejected():
    flash = _QWEN_TTS_BY_NAME[QWEN_TTS_FLASH]()
    with pytest.raises(ValidationError):
        flash.validate(GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", voice_id="longanlingxin"))
    with pytest.raises(ValidationError):
        flash.validate(GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", speed=3))


def test_instruction_uses_han_weight():
    model = _QWEN_TTS_BY_NAME[QWEN_TTS_FLASH]()
    model.validate(GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", mood="你" * 50))
    with pytest.raises(ValidationError):
        model.validate(GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", mood="你" * 51))
    model.validate(GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", mood="a" * 100))
    with pytest.raises(ValidationError):
        model.validate(GenerationRequest(task_type=TaskType.SPEECH, prompt="你好", mood="a" * 101))


def test_weighted_chars_match_the_guide():
    assert qwen_weighted_chars("你好") == 4
    assert qwen_weighted_chars("中A文123") == 8
    assert qwen_weighted_chars("中文。") == 5


def test_character_rates_and_region():
    assert qwen_tts_region(_BEIJING) == "cn"
    assert qwen_tts_region(_INTL) == "intl"
    assert estimate_qwen_tts_points(QWEN_TTS_FLASH, "", base_url=_BEIJING) == 1
    assert estimate_qwen_tts_points(QWEN_TTS_FLASH, "a" * 100, base_url=_BEIJING) == 1
    assert estimate_qwen_tts_points(QWEN_TTS_FLASH, "a" * 101, base_url=_BEIJING) == 2
    assert estimate_qwen_tts_points(QWEN_TTS_FLASH, "a" * 10000, base_url=_BEIJING) == 100
    assert estimate_qwen_tts_points(QWEN_TTS_FLASH, "a" * 10000, base_url=_INTL) == 113
    assert estimate_qwen_tts_points(QWEN_TTS_PLUS, "a" * 10000, base_url=_BEIJING) == 140
    assert estimate_qwen_tts_points(QWEN_TTS_FLASH, "你" * 20000, base_url=_BEIJING) == 400


def test_token_estimate_and_settle():
    short = estimate_qwen_tts_points(QWEN_TTS_31, "", base_url=_BEIJING)
    assert short == 1
    long = estimate_qwen_tts_points(QWEN_TTS_31, "你" * 20000, base_url=_BEIJING)
    assert long == 303
    assert (
        settle_qwen_tts_points(
            QWEN_TTS_31,
            {"input_tokens": 1_000_000, "output_tokens": 0},
            base_url=_BEIJING,
        )
        == 150
    )
    assert (
        settle_qwen_tts_points(
            QWEN_TTS_31,
            {"input_tokens": 1_000_000, "output_tokens": 0},
            base_url=_INTL,
        )
        == 168
    )
    assert (
        settle_qwen_tts_points(
            QWEN_TTS_31,
            {"input_tokens": 0, "output_tokens": 1_000_000},
            base_url=_INTL,
        )
        == 1364
    )
    assert settle_qwen_tts_points(QWEN_TTS_31, {"characters": 10000}, base_url=_BEIJING) is None
    assert settle_qwen_tts_points(QWEN_TTS_FLASH, {}, base_url=_BEIJING) is None
    assert (
        settle_qwen_tts_points(
            QWEN_TTS_FLASH,
            {"payload": {"usage": {"characters": 10000}}},
            base_url=_BEIJING,
        )
        == 100
    )
    assert settle_qwen_tts_points(QWEN_TTS_PLUS, {"characters": 10000}, base_url=_INTL) == 150


def test_ws_url_follows_the_configured_host():
    assert dashscope_tts_ws_url(_BEIJING) == "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    assert dashscope_tts_ws_url(_INTL) == "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    assert dashscope_tts_ws_url("http://dashscope.aliyuncs.com/api/v1") == (
        "ws://dashscope.aliyuncs.com/api-ws/v1/inference"
    )


def test_wire_body_omits_empty_instruction():
    plain = qwen_tts_wire_body(model=QWEN_TTS_FLASH, text="你好", voice="longanhuan_v3.6", rate=1.0, instruction="")
    assert "instruction" not in plain
    styled = qwen_tts_wire_body(
        model=QWEN_TTS_FLASH,
        text="你好",
        voice="longanhuan_v3.6",
        rate=1.5,
        instruction="轻快",
    )
    assert "instruction" in styled
    assert styled["instruction"] == "轻快"
    assert styled["rate"] == 1.5


class _ScriptedSocket:
    def __init__(self, script: list[str | bytes]) -> None:
        self.script = list(script)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if not self.script:
            raise RuntimeError("no more frames")
        return self.script.pop(0)

    async def close(self) -> None:
        self.closed = True


def _actions(sock: _ScriptedSocket) -> list[str]:
    names: list[str] = []
    for raw in sock.sent:
        parsed: object = json.loads(raw)
        assert isinstance(parsed, dict)
        header = parsed["header"]
        assert isinstance(header, dict)
        action = header["action"]
        assert isinstance(action, str)
        names.append(action)
    return names


def test_websocket_order_and_binary_audio(monkeypatch):
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg

    sock = _ScriptedSocket(
        [
            json.dumps({"header": {"event": "task-started"}}),
            b"\xff\xfb",
            b"\x00\x01",
            json.dumps(
                {
                    "header": {"event": "task-finished"},
                    "payload": {"usage": {"characters": 4}},
                }
            ),
        ]
    )
    seen: list[tuple[str, str]] = []

    async def _connect(url: str, api_key: str) -> _ScriptedSocket:
        seen.append((url, api_key))
        return sock

    def _cfg(key: str) -> object:
        if key == "HappyHorse_Enable_dashscope":
            return True
        if key == "HappyHorse_apikey_dashscope":
            return "sk-test-key-1234567890"
        if key == "HappyHorse_BaseURL_dashscope":
            return _BEIJING
        return None

    monkeypatch.setattr(dcfg, "_cfg", _cfg)
    audio = asyncio.run(
        DashScopeSpeechAPI(connect=_connect).synthesize(
            model=QWEN_TTS_FLASH,
            text="你好",
            voice="longanhuan_v3.6",
            rate=1.0,
            instruction="",
        )
    )
    assert seen == [("wss://dashscope.aliyuncs.com/api-ws/v1/inference", "sk-test-key-1234567890")]
    assert _actions(sock) == ["run-task", "continue-task", "finish-task"]
    assert "sk-test-key-1234567890" not in "".join(sock.sent)
    run = json.loads(sock.sent[0])
    assert run["payload"]["parameters"]["enable_ssml"] is False
    assert "instruction" not in run["payload"]["parameters"]
    assert run["payload"]["model"] == QWEN_TTS_FLASH
    assert json.loads(sock.sent[1])["payload"]["input"]["text"] == "你好"
    assert audio.audio == b"\xff\xfb\x00\x01"
    assert audio.usage == {"characters": 4}
    assert sock.closed is True
    task_ids = [json.loads(item)["header"]["task_id"] for item in sock.sent]
    assert task_ids[0] == task_ids[1] == task_ids[2]


def test_task_failed_raises(monkeypatch):
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg

    sock = _ScriptedSocket([json.dumps({"header": {"event": "task-failed", "error_message": "InvalidParameter"}})])

    async def _connect(url: str, api_key: str) -> _ScriptedSocket:
        _ = (url, api_key)
        return sock

    def _cfg(key: str) -> object:
        if key == "HappyHorse_Enable_dashscope":
            return True
        if key == "HappyHorse_apikey_dashscope":
            return "sk-test-key-1234567890"
        if key == "HappyHorse_BaseURL_dashscope":
            return _INTL
        return None

    monkeypatch.setattr(dcfg, "_cfg", _cfg)
    with pytest.raises(RuntimeError, match="InvalidParameter"):
        asyncio.run(
            DashScopeSpeechAPI(connect=_connect).synthesize(
                model=QWEN_TTS_PLUS,
                text="你好",
                voice="longanlingxin",
                rate=1.0,
                instruction="轻快",
            )
        )


def test_old_dashscope_list_leaves_tts_off(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg

    saved = ["happyhorse1.1", "wan3.0"]
    monkeypatch.setattr(dcfg, "_cfg", lambda key: saved if key == "DashScope_Enabled_Models" else True)
    monkeypatch.setattr(el, "_cfg", lambda key: saved if key == "DashScope_Enabled_Models" else True)
    model = _QWEN_TTS_BY_NAME[QWEN_TTS_FLASH]()
    assert dcfg.is_dashscope_model_enabled(QWEN_TTS_FLASH) is False
    assert dcfg.is_dashscope_model_enabled("wan3.0") is True
    assert asyncio.run(model.check_available()) is False
    reason = asyncio.run(model.unavailable_reason())
    assert QWEN_TTS_FLASH in reason
    gate = AdapterChannel("dashscope_tts", model_name=QWEN_TTS_FLASH)._enable_gate()
    assert gate is not None
    assert QWEN_TTS_FLASH in gate


def test_empty_dashscope_list_closes_tts(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el

    monkeypatch.setattr(el, "_cfg", lambda key: [])
    gate = AdapterChannel("dashscope_tts", model_name=QWEN_TTS_31)._enable_gate()
    assert gate is not None
    assert QWEN_TTS_31 in gate
