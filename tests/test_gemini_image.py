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


def test_relay_base_url_passed_to_sdk(monkeypatch):
    """服务器直连不到 Google 时靠中转地址走通;留空则直连官方端点。"""
    api = _api_with(
        monkeypatch,
        {"Gemini_Image_apikey": "AIzaKEY", "Gemini_Image_BaseURL": "https://relay.invalid/gemini/"},
    )
    client = api._build_client()
    opts = client._api_client._http_options
    assert opts.base_url == "https://relay.invalid/gemini/"
    assert opts.api_version == "v1beta"  # SDK 仍在其后拼 /v1beta/...,中转端按标准路径转发

    plain = _api_with(monkeypatch, {"Gemini_Image_apikey": "AIzaKEY"})
    assert "generativelanguage" in plain._build_client()._api_client._http_options.base_url


def test_relay_base_url_ignored_in_vertex_mode(monkeypatch):
    """Vertex 有自己的端点体系,套 AI Studio 的中转前缀只会把它打歪。"""
    api = _api_with(
        monkeypatch,
        {
            "Gemini_Image_Use_Vertex": True,
            "Gemini_Image_Project_ID": "proj-1",
            "Gemini_Image_BaseURL": "https://relay.invalid/gemini/",
        },
    )
    base = api._build_client()._api_client._http_options.base_url
    assert "relay.invalid" not in base


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


def test_banana1_served_by_gemini_first_gen():
    # Nano Banana 1 = 一代 Gemini 模型(gemini-2.5-flash-image),与 banana2
    # 共用同一条 GeminiImageChannel;外部插件可经 channel_registry 追加供应商
    from RH_ComfyUI.models.image.defs import Banana1Def

    channel_registry.clear()
    banana1 = Banana1Def()
    names = [b.channel.name for b in banana1.channel_bindings()]
    assert names == ["gemini"]
    assert banana1.channel_bindings()[0].vendor_model == "gemini-2.5-flash-image"
    schema = banana1.input_schema()
    # 一代不支持尺寸档:schema 只有 ratio,无 image_size
    assert "ratio" in schema and "image_size" not in schema


def test_mapper_omits_image_size_for_first_gen(monkeypatch):
    # 回归:一代不支持 image_config.image_size,mapper 必须整个字段不发;
    # 3.x 系保持既有默认 2K
    from typing import Optional

    import RH_ComfyUI.utils.mappers.gemini_image as gmapper
    from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
    from RH_ComfyUI.utils.backends.gemini_image.api import GeminiImageAPI

    captured: list[Optional[str]] = []

    class _FakeApi(GeminiImageAPI):
        async def generate(
            self,
            *,
            model: str,
            prompt: str,
            images: Optional[list[bytes]] = None,
            aspect_ratio: str = "1:1",
            image_size: Optional[str] = "2K",
        ) -> bytes:
            captured.append(image_size)
            return b"IMG"

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req.params["model"] = "gemini-2.5-flash-image"
    asyncio.run(gmapper.gemini_flash_image_mapper(req, _FakeApi()))
    assert captured[0] is None

    req2 = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req2.params["model"] = "gemini-3.1-flash-image-preview"
    asyncio.run(gmapper.gemini_flash_image_mapper(req2, _FakeApi()))
    assert captured[1] == "2K"

    # 显式传 image_size 时(如 banana2 的端口)原样透传
    req3 = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req3.params["model"] = "gemini-3.1-flash-image-preview"
    req3.params["image_size"] = "4K"
    asyncio.run(gmapper.gemini_flash_image_mapper(req3, _FakeApi()))
    assert captured[2] == "4K"


def test_vertex_invoke_passes_guard_without_api_key(monkeypatch):
    # 回归:Vertex 模式(ADC/SA 鉴权)合法地没有 api_key,invoke 的守卫
    # 必须与 check_available 同源用 is_configured(),不能按 api_key 拒绝
    import RH_ComfyUI.utils.backends.gemini_image.channel as gchan
    from RH_ComfyUI.core.schema.types import NodeOutput
    from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest

    monkeypatch.setattr(
        gapi,
        "SERVICE_CONFIG",
        _FakeConfig({"Gemini_Image_Use_Vertex": True, "Gemini_Image_Project_ID": "proj-1"}),
    )

    async def _fake_mapper(request, api):
        return NodeOutput(output_type="image", data=b"IMG")

    monkeypatch.setattr(gchan, "gemini_flash_image_mapper", _fake_mapper)
    ch = GeminiImageChannel()
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    out = asyncio.run(ch.invoke(request=req, vendor_model="gemini-3.1-flash-image-preview"))
    assert out.data == b"IMG"
    assert out.metadata["channel"] == "gemini-vertex"


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
