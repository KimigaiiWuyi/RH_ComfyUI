"""Gemini 生图后端(google-genai SDK / Interactions API)— 双模判定 + 图片解析 + 接线"""

import base64
import asyncio
from types import SimpleNamespace

import RH_ComfyUI.utils.backends.gemini_image.api as gapi
from RH_ComfyUI.core import channel_registry
from RH_ComfyUI.models.image.defs import Banana2Def
from RH_ComfyUI.utils.backends.gemini_image.channel import GeminiImageChannel


class _FakeVal:
    def __init__(self, data: str) -> None:
        self.data = data


class _FakeConfig:
    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get_config(self, key: str) -> _FakeVal:
        return _FakeVal(self._m.get(key, ""))


def _api_with(monkeypatch, mapping: dict) -> gapi.GeminiImageAPI:
    monkeypatch.setattr(gapi, "SERVICE_CONFIG", _FakeConfig(mapping))
    return gapi.GeminiImageAPI()


def test_ai_studio_mode(monkeypatch):
    api = _api_with(monkeypatch, {"Gemini_Image_apikey": "AIzaKEY"})
    assert api.is_vertex is False
    assert api.is_configured() is True


def test_ai_studio_unconfigured(monkeypatch):
    api = _api_with(monkeypatch, {})
    assert api.is_vertex is False
    assert api.is_configured() is False


def test_vertex_mode_requires_toggle(monkeypatch):
    # 只填 project 不开开关 → 仍是 AI Studio(避免忽略 api_key 的坑)
    api = _api_with(monkeypatch, {"Gemini_Image_Project_ID": "proj-1"})
    assert api.is_vertex is False

    api2 = _api_with(
        monkeypatch,
        {
            "Gemini_Image_Use_Vertex": True,
            "Gemini_Image_Project_ID": "proj-1",
            "Gemini_Image_Location": "global",
        },
    )
    assert api2.is_vertex is True
    assert api2.is_configured() is True
    assert api2.location == "global"


def test_find_image_inline_and_uri():
    b64 = base64.b64encode(b"PNGDATA").decode()
    interaction = SimpleNamespace(
        outputs=[
            SimpleNamespace(type="text", data=None, uri=None, text="hi"),
            SimpleNamespace(type="image", data=b64, uri=None),
        ],
    )
    assert gapi._find_image(interaction) == (b"PNGDATA", None)

    uri_only = SimpleNamespace(outputs=[SimpleNamespace(type="image", data=None, uri="http://x/a.png")])
    assert gapi._find_image(uri_only) == (None, "http://x/a.png")

    assert gapi._find_image(SimpleNamespace(outputs=[])) == (None, None)
    assert gapi._find_image(SimpleNamespace(outputs=None)) == (None, None)


def test_find_image_in_steps():
    # 真实响应形态:outputs 空,图在 steps[*].content[*](model_output 步)
    b64 = base64.b64encode(b"IMG").decode()
    interaction = SimpleNamespace(
        outputs=[],
        steps=[
            {"type": "thought", "signature": "xxx"},
            {"type": "model_output", "content": [{"type": "image", "data": b64}]},
        ],
    )
    assert gapi._find_image(interaction) == (b"IMG", None)


def test_banana2_served_by_gemini_only():
    # Nano Banana 2 走原生 Gemini,不再经过 gpt-image-2 后端。
    channel_registry.clear()
    banana2 = Banana2Def()
    assert banana2.display_name == "Nano Banana 2"
    names = [b.channel.name for b in banana2.channel_bindings()]
    assert names == ["gemini"]
    assert "gpt-image-2" not in names
    assert banana2.channel_bindings()[0].vendor_model == "gemini-3.1-flash-image-preview"
    assert banana2.node.backend == "gemini-image"
    # 面向前端的 schema:ratio + image_size(Gemini 实际参数),不再是宽高
    schema = banana2.input_schema()
    assert "ratio" in schema and "image_size" in schema
    assert "width" not in schema and "height" not in schema


def test_gemini_channel_availability(monkeypatch):
    ch = GeminiImageChannel()
    monkeypatch.setattr(gapi, "SERVICE_CONFIG", _FakeConfig({}))
    assert asyncio.run(ch.check_available()) is False
    monkeypatch.setattr(gapi, "SERVICE_CONFIG", _FakeConfig({"Gemini_Image_apikey": "AIzaKEY"}))
    assert asyncio.run(ch.check_available()) is True
    assert ch.audit_key_prefix() == "AIzaKE"
    # Vertex(开开关):无 key 但有 project 也算可用,审计记 project 前缀
    monkeypatch.setattr(
        gapi,
        "SERVICE_CONFIG",
        _FakeConfig({"Gemini_Image_Use_Vertex": True, "Gemini_Image_Project_ID": "projxyz"}),
    )
    assert asyncio.run(ch.check_available()) is True
    assert ch.audit_key_prefix() == "projxy"
