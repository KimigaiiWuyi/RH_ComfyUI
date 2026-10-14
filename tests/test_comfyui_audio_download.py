"""RunningHub 音频产物:子目录 /view 404 时改走 output 根或 outputs 接口。"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path

import httpx

from RH_ComfyUI.utils.backends.comfyui.api import (
    ComfyUIAPI,
    _tos_public_url,
    _view_subfolder,
    _pick_output_url,
    _view_candidates,
    _body_matches_filename,
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
    """SaveAudioMP3 的 audio/ 前缀会让 RH COS key 变成 output/audio/,该对象 404。"""
    specs = (
        ("RH_ComfyUI/utils/resource/workflow/语音生成/IndexTTS2.json", "39"),
        ("RH_ComfyUI/utils/resource/workflow/音乐生成/ace_step1.5.json", "134"),
    )
    for rel, node_id in specs:
        workflow = json.loads((_ROOT / rel).read_text(encoding="utf-8"))
        node = workflow[node_id]
        assert node["class_type"] == "SaveAudioMP3"
        assert node["inputs"]["quality"] == "V0"
        prefix = node["inputs"]["filename_prefix"]
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


_TENANT = "17bb5f29a59d7a276e41f2e24c0bf403"
_TOS_ITEM: dict[str, object] = {
    "bucket": "rh-comfyui01",
    "storageProvider": "tos",
    "filename": _MP3,
    "objectKey": f"{_TENANT}/output/{_MP3}",
    "subfolder": f"{_TENANT}/",
    "type": "output",
}


def test_tos_public_url_matches_ai_app_host() -> None:
    url = _tos_public_url(_TOS_ITEM)
    assert url == f"https://rh-comfyui01.tos-cn-beijing.volces.com/{_TENANT}/output/{_MP3}"


def test_view_candidates_drop_tenant_hash() -> None:
    assert _view_candidates(f"{_TENANT}/", _TOS_ITEM, True) == [""]
    assert _view_candidates("audio", {}, True) == ["audio", ""]


def test_json_error_page_is_not_audio() -> None:
    assert _body_matches_filename(b'{"code":404}', _MP3) is False
    assert _body_matches_filename(b"", _MP3) is False
    assert _body_matches_filename(b"ID3" + b"\x00" * 8, _MP3) is True


def test_runninghub_tos_object_skips_bad_view(monkeypatch) -> None:
    """history 只有 objectKey 时先下 TOS。/view 错页不能当成 mp3。"""
    api = ComfyUIAPI()
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))
    viewed: list[str] = []

    async def fake_download(self: ComfyUIAPI, url: str, *, retry_attempts: int = 5) -> bytes:
        del self
        assert retry_attempts == 1
        assert url == f"https://rh-comfyui01.tos-cn-beijing.volces.com/{_TENANT}/output/{_MP3}"
        return b"ID3real-audio"

    async def fake_view(self: ComfyUIAPI, filename: str, subfolder: str, folder_type: str) -> bytes:
        del self, filename, folder_type
        viewed.append(subfolder)
        return b'{"code":404,"msg":"NoSuchKey"}'

    monkeypatch.setattr(ComfyUIAPI, "_download_url", fake_download)
    monkeypatch.setattr(ComfyUIAPI, "_fetch_view_once", fake_view)

    async def _run() -> bytes:
        return await api.get_file(
            _MP3,
            f"{_TENANT}/",
            "output",
            prompt_id="2110277119576866818",
            source=_TOS_ITEM,
        )

    assert asyncio.run(_run()) == b"ID3real-audio"
    assert viewed == []


def test_runninghub_tos_failure_falls_back_to_view(monkeypatch) -> None:
    """直链 5xx 或传输错误时继续 /view,不能把整次取文件判失败。"""
    api = ComfyUIAPI()
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))
    request = httpx.Request("GET", "https://example.invalid/a.mp3")
    failures: tuple[BaseException, ...] = (
        httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request)),
        httpx.ConnectError("tos down"),
    )
    for failure in failures:
        viewed: list[str] = []

        async def fake_download(
            self: ComfyUIAPI,
            url: str,
            *,
            retry_attempts: int = 5,
            exc: BaseException = failure,
        ) -> bytes:
            del self, url
            assert retry_attempts == 1
            raise exc

        async def fake_view(self: ComfyUIAPI, filename: str, subfolder: str, folder_type: str) -> bytes:
            del self, filename, folder_type
            viewed.append(subfolder)
            return b"ID3ok"

        monkeypatch.setattr(ComfyUIAPI, "_download_url", fake_download)
        monkeypatch.setattr(ComfyUIAPI, "_fetch_view_once", fake_view)

        async def _run() -> bytes:
            return await api.get_file(
                _MP3,
                f"{_TENANT}/",
                "output",
                prompt_id="pid",
                source=_TOS_ITEM,
            )

        assert asyncio.run(_run()) == b"ID3ok"
        assert viewed == [""]


def test_runninghub_view_error_page_falls_through(monkeypatch) -> None:
    api = ComfyUIAPI()
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))
    calls: list[str] = []

    async def fake_view(self: ComfyUIAPI, filename: str, subfolder: str, folder_type: str) -> bytes:
        del self, filename, folder_type
        calls.append(subfolder)
        if subfolder == "audio":
            return b'{"code":404}'
        return b"ID3ok"

    monkeypatch.setattr(ComfyUIAPI, "_fetch_view_once", fake_view)

    async def _run() -> bytes:
        return await api.get_file(_MP3, "audio", "output", prompt_id="pid")

    assert asyncio.run(_run()) == b"ID3ok"
    assert calls == ["audio", ""]
