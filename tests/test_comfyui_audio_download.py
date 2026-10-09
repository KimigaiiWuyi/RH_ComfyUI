"""RunningHub 音频产物:子目录 /view 404 时改走 output 根或 outputs 接口。"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path

import httpx

from RH_ComfyUI.utils.backends.comfyui.api import (
    ComfyUIAPI,
    _view_subfolder,
    _pick_output_url,
    _parse_runninghub_output_payload,
)

_ROOT = Path(__file__).resolve().parents[1]
_MP3 = "ComfyUI_00001_ztlbh_1791534431.mp3"


def _not_found(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(404, request=request)
    return httpx.HTTPStatusError("404", request=request, response=response)


def test_view_subfolder_treats_dot_as_output_root() -> None:
    assert _view_subfolder("") == ""
    assert _view_subfolder(".") == ""
    assert _view_subfolder(None) == ""
    assert _view_subfolder("audio") == "audio"
    assert _view_subfolder(Path("audio")) == "audio"


def test_parse_runninghub_outputs_file_url() -> None:
    payload = {
        "code": 0,
        "data": [
            {
                "fileUrl": "https://rh-images.xiaoyaoyou.com/hash/output/a.mp3",
                "fileType": "audio",
            }
        ],
    }
    code, urls = _parse_runninghub_output_payload(payload)
    assert code == 0
    assert urls == ["https://rh-images.xiaoyaoyou.com/hash/output/a.mp3"]


def test_pick_output_url_matches_filename() -> None:
    urls = [
        "https://cdn.example/other.mp3",
        f"https://cdn.example/output/{_MP3}",
    ]
    picked = _pick_output_url(urls, _MP3)
    assert picked == f"https://cdn.example/output/{_MP3}"


def test_audio_workflows_save_at_output_root() -> None:
    """SaveAudio 默认 audio/ 前缀会让 RH COS key 变成 output/audio/,该对象 404。"""
    specs = (
        ("RH_ComfyUI/utils/resource/workflow/语音生成/IndexTTS2.json", "39"),
        ("RH_ComfyUI/utils/resource/workflow/音乐生成/ace_step1.5.json", "104"),
    )
    for rel, node_id in specs:
        workflow = json.loads((_ROOT / rel).read_text(encoding="utf-8"))
        prefix = workflow[node_id]["inputs"]["filename_prefix"]
        assert isinstance(prefix, str)
        assert "/" not in prefix and "\\" not in prefix


def test_runninghub_audio_404_retries_without_subfolder(monkeypatch) -> None:
    api = ComfyUIAPI()
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))
    calls: list[str] = []

    async def fake_view(self: ComfyUIAPI, filename: str, subfolder: str, folder_type: str) -> bytes:
        del self, filename, folder_type
        calls.append(subfolder)
        if subfolder == "audio":
            raise _not_found("https://rh-images.xiaoyaoyou.com/hash/output/audio/a.mp3")
        return b"ID3"

    monkeypatch.setattr(ComfyUIAPI, "_fetch_view_once", fake_view)

    async def _run() -> bytes:
        return await api.get_file(_MP3, "audio", "output", prompt_id="pid")

    assert asyncio.run(_run()) == b"ID3"
    assert calls == ["audio", ""]


def test_runninghub_audio_404_uses_openapi_outputs(monkeypatch) -> None:
    api = ComfyUIAPI()
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    async def fake_view(self: ComfyUIAPI, filename: str, subfolder: str, folder_type: str) -> bytes:
        del self, filename, subfolder, folder_type
        raise _not_found("https://rh-images.xiaoyaoyou.com/hash/output/audio/a.mp3")

    async def fake_outputs(self: ComfyUIAPI, task_id: str, filename: str) -> bytes:
        del self
        assert task_id == "pid"
        assert filename == _MP3
        return b"from-outputs"

    monkeypatch.setattr(ComfyUIAPI, "_fetch_view_once", fake_view)
    monkeypatch.setattr(ComfyUIAPI, "_download_runninghub_task_output", fake_outputs)

    async def _run() -> bytes:
        return await api.get_file(_MP3, "audio", "output", prompt_id="pid")

    assert asyncio.run(_run()) == b"from-outputs"
