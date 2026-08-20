"""IndexTTS2.5: RunningHub AI 应用接入(声明式 nodeInfoList + 参考音频上传)。"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.models.speech.defs import ALL_MODELS, IndexTTS25Def
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.rh_app.executor import RHAppAdapter, _guess_audio_upload_name


def test_index_tts25_in_all_models():
    assert IndexTTS25Def in ALL_MODELS
    node = IndexTTS25Def.node_def()
    assert node.name == "IndexTTS2.5"
    assert node.display_name == "IndexTTS2.5"
    assert node.backend == "rh_app"
    assert node.workflow_file == "2089692922081009666"
    assert node.mode == "declarative"
    assert node.task_type == TaskType("speech")
    assert node.capabilities.mode == "async_poll"
    assert "prompt" in node.inputs
    assert "reference_audio" in node.inputs
    assert "mood" in node.inputs
    assert node.inputs["prompt"].required is True


def test_index_tts25_mappings_target_runninghub_nodes():
    node = IndexTTS25Def.node_def()
    by_source = {m["source"]: m for m in node.mappings}
    assert by_source["reference_audio"]["target"] == "2.audio"
    assert by_source["reference_audio"]["type"] == "upload_audio"
    assert by_source["reference_audio"]["optional"] is True
    assert by_source["prompt"]["target"] == "6.prompt"
    assert by_source["mood"]["target"] == "8.text"
    assert by_source["mood"]["default"] == ""


def test_index_tts25_rh_app_cancel_flags():
    m = IndexTTS25Def()
    assert m.supports_cancel is False
    assert m.supports_remote_cancel is False


def test_guess_audio_upload_name():
    assert _guess_audio_upload_name(b"ID3....mp3") == "input.mp3"
    assert _guess_audio_upload_name(b"RIFF....WAVE") == "input.wav"
    assert _guess_audio_upload_name(b"OggS....") == "input.ogg"
    assert _guess_audio_upload_name(b"\x00\x00\x00\x20ftypisom") == "input.m4a"


class _FakeUploadAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str]] = []

    async def upload_file(self, file_data: bytes, filename: str = "input.png") -> str:
        self.calls.append((file_data, filename))
        return f"uploaded-{filename}"


def test_index_tts25_builds_node_info_list_with_audio_and_mood():
    adapter = RHAppAdapter()
    api = _FakeUploadAPI()
    adapter.api = api  # type: ignore[assignment]
    node = IndexTTS25Def.node_def()
    audio = b"ID3fake-mp3"
    req = GenerationRequest(
        task_type=TaskType.SPEECH,
        prompt="龙傲宇静默不语。",
        mood="开心",
        reference_audio=audio,
    )

    info = asyncio.run(adapter._build_node_info_list(req, node))
    by_field = {(item["nodeId"], item["fieldName"]): item for item in info}
    assert by_field[("2", "audio")]["fieldValue"] == "uploaded-input.mp3"
    assert by_field[("2", "audio")]["description"] == "audio"
    assert by_field[("6", "prompt")]["fieldValue"] == "龙傲宇静默不语。"
    assert by_field[("8", "text")]["fieldValue"] == "开心"
    assert api.calls == [(audio, "input.mp3")]


def test_index_tts25_omits_audio_and_sends_empty_emotion():
    adapter = RHAppAdapter()
    api = _FakeUploadAPI()
    adapter.api = api  # type: ignore[assignment]
    node = IndexTTS25Def.node_def()
    req = GenerationRequest(task_type=TaskType.SPEECH, prompt="你好")

    info = asyncio.run(adapter._build_node_info_list(req, node))
    by_field = {(item["nodeId"], item["fieldName"]): item for item in info}
    assert ("2", "audio") not in by_field
    assert by_field[("6", "prompt")]["fieldValue"] == "你好"
    assert by_field[("8", "text")]["fieldValue"] == ""
    assert api.calls == []
