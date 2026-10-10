"""Gemini 生图后端(google-genai SDK / Interactions API)— 双模判定 + 图片解析 + 接线"""

import json
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


def test_snap_gemini_aspect_ratio_keeps_whitelist_and_folds_8_5():
    from RH_ComfyUI.utils.mappers.gemini_image import snap_gemini_aspect_ratio

    assert snap_gemini_aspect_ratio("16:9") == "16:9"
    assert snap_gemini_aspect_ratio("auto") == "1:1"
    assert snap_gemini_aspect_ratio("") == "1:1"
    # 8:5=1.6,白名单里最近是 3:2=1.5(16:9=1.778 更远)
    assert snap_gemini_aspect_ratio("8:5") == "3:2"


def test_mapper_snaps_8_5_before_generate(monkeypatch):
    import RH_ComfyUI.utils.mappers.gemini_image as gmapper
    from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest

    seen: list[str] = []

    class _FakeApi:
        async def generate(
            self,
            *,
            model: str,
            prompt: str,
            images: list[bytes] | None = None,
            aspect_ratio: str = "1:1",
            image_size: str | None = "2K",
            thinking_level: str | None = None,
            background: bool = True,
            poll_interval: float = 1.5,
            max_wait: float = 600.0,
        ) -> bytes:
            seen.append(aspect_ratio)
            return b"IMG"

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", ratio="8:5")
    asyncio.run(gmapper.gemini_flash_image_mapper(req, _FakeApi()))
    assert seen == ["3:2"]


def test_canonical_image_model_strips_agent_suffix():
    assert gapi._canonical_image_model("gemini-3.1-flash-image-preview-agent") == ("gemini-3.1-flash-image-preview")
    assert gapi._canonical_image_model("gemini-3.1-flash-image-preview") == ("gemini-3.1-flash-image-preview")


def test_banana2_served_by_gemini_only():
    # Nano Banana 2 仍是 Gemini 3.1 Flash,不换成 2.1。
    channel_registry.clear()
    banana2 = Banana2Def()
    assert banana2.name == "banana2"
    assert banana2.display_name == "Nano Banana 2"
    names = [b.channel.name for b in banana2.channel_bindings()]
    assert names == ["gemini"]
    assert "gpt-image-2" not in names
    assert banana2.channel_bindings()[0].vendor_model == "gemini-3.1-flash-image-preview"
    assert banana2.node.backend == "gemini-image"
    assert banana2.execution_mode == "sync"
    schema = banana2.input_schema()
    assert "ratio" in schema and "image_size" in schema
    assert "thinking_level" not in schema
    assert "width" not in schema and "height" not in schema
    assert schema["image_size"].values == ["512", "1K", "2K", "4K"]
    assert schema["image_size"].default == "2K"
    assert schema["ratio"].values == ["1:1", "16:9", "8:5", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]


def test_banana21_served_by_gemini_only():
    # Nano Banana 2.1 单独成模型,与 banana2 并存。
    from RH_ComfyUI.models.image.defs import Banana21Def

    channel_registry.clear()
    model = Banana21Def()
    assert model.name == "banana2.1"
    assert model.display_name == "Nano Banana 2.1"
    names = [b.channel.name for b in model.channel_bindings()]
    assert names == ["gemini"]
    assert model.channel_bindings()[0].vendor_model == "gemini-nano-banana-2.1"
    assert model.node.backend == "gemini-image"
    schema = model.input_schema()
    assert "thinking_level" in schema
    assert schema["image_size"].values == ["1K", "2K", "4K"]
    assert schema["image_size"].default == "1K"
    assert schema["thinking_level"].values == ["minimal", "medium", "high"]
    assert schema["thinking_level"].default == "medium"
    for ratio in ("1:4", "4:1", "1:8", "8:1", "4:5", "5:4", "8:5"):
        assert ratio in (schema["ratio"].values or [])
    assert "1:4" not in (Banana2Def().input_schema()["ratio"].values or [])


def test_banana1_served_by_gemini_first_gen():
    # Nano Banana 1 = 一代 Gemini 模型(gemini-2.5-flash-image),与 banana2
    # 共用同一条 GeminiImageChannel;外部插件可经 channel_registry 追加供应商
    from RH_ComfyUI.models.image.defs import Banana1Def

    channel_registry.clear()
    banana1 = Banana1Def()
    names = [b.channel.name for b in banana1.channel_bindings()]
    assert names == ["gemini"]
    assert banana1.channel_bindings()[0].vendor_model == "gemini-2.5-flash-image"
    assert banana1.execution_mode == "sync"
    schema = banana1.input_schema()
    # 一代不支持尺寸档:schema 只有 ratio,无 image_size
    assert "ratio" in schema and "image_size" not in schema


def test_banana_pro_includes_gemini_pro_image():
    # banana_pro 内置 Gemini 3 Pro Image + gpt-image-2 适配,外部可再注入
    from RH_ComfyUI.models.image.defs import BananaProDef

    channel_registry.clear()
    pro = BananaProDef()
    bindings = pro.channel_bindings()
    names = [b.channel.name for b in bindings]
    assert names[0] == "gemini"
    assert bindings[0].vendor_model == "gemini-3-pro-image-preview"
    assert "gpt-image-2" in names
    assert pro.GEMINI_VENDOR_MODEL == "gemini-3-pro-image-preview"
    assert pro.execution_mode == "sync"


def test_gemini_channel_marks_429_as_transient():
    from RH_ComfyUI.utils.backends.gemini_image.channel import _is_rate_limited

    assert _is_rate_limited(RuntimeError('Resource exhausted code":429'))
    assert _is_rate_limited(RuntimeError("HTTP 503 Service Unavailable"))
    assert not _is_rate_limited(RuntimeError("invalid argument"))


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
            thinking_level: Optional[str] = None,
        ) -> bytes:
            captured.append(image_size)
            return b"IMG"

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req.params["model"] = "gemini-2.5-flash-image"
    asyncio.run(gmapper.gemini_flash_image_mapper(req, _FakeApi()))
    assert captured[0] is None

    req2 = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req2.params["model"] = "gemini-3-pro-image-preview"
    asyncio.run(gmapper.gemini_flash_image_mapper(req2, _FakeApi()))
    assert captured[1] == "2K"

    # 2.1 官方默认 1K;显式档位原样透传
    req3 = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req3.params["model"] = "gemini-nano-banana-2.1"
    asyncio.run(gmapper.gemini_flash_image_mapper(req3, _FakeApi()))
    assert captured[2] == "1K"

    req4 = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req4.params["model"] = "gemini-nano-banana-2.1"
    req4.params["image_size"] = "4K"
    asyncio.run(gmapper.gemini_flash_image_mapper(req4, _FakeApi()))
    assert captured[3] == "4K"

    # banana2 的 flash preview 保持默认 2K
    req5 = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    req5.params["model"] = "gemini-3.1-flash-image-preview"
    asyncio.run(gmapper.gemini_flash_image_mapper(req5, _FakeApi()))
    assert captured[4] == "2K"


def test_mapper_sends_thinking_only_for_nano_banana_21():
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
            thinking_level: Optional[str] = None,
        ) -> bytes:
            captured.append(thinking_level)
            return b"IMG"

    old = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    old.params["model"] = "gemini-3-pro-image-preview"
    old.params["thinking_level"] = "high"
    asyncio.run(gmapper.gemini_flash_image_mapper(old, _FakeApi()))
    assert captured[0] is None

    flash = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    flash.params["model"] = "gemini-3.1-flash-image-preview"
    flash.params["thinking_level"] = "high"
    asyncio.run(gmapper.gemini_flash_image_mapper(flash, _FakeApi()))
    assert captured[1] is None

    new = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", ratio="1:8")
    new.params["model"] = "gemini-nano-banana-2.1"
    new.params["thinking_level"] = "high"
    asyncio.run(gmapper.gemini_flash_image_mapper(new, _FakeApi()))
    assert captured[2] == "high"

    default = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    default.params["model"] = "gemini-nano-banana-2.1"
    asyncio.run(gmapper.gemini_flash_image_mapper(default, _FakeApi()))
    assert captured[3] == "medium"


def test_nano_banana_21_generate_content_config(monkeypatch):
    """2.1 仍走 generate_content:image_config 带新比例,thinking_config 带思考档。"""
    from google.genai import types

    seen: dict[str, object] = {}

    class _Part:
        def __init__(self) -> None:
            self.inline_data = type("D", (), {"data": b"\x89PNG", "uri": None})()
            self.file_data = None
            self.text = None

    class _Response:
        def __init__(self) -> None:
            self.candidates = [type("C", (), {"content": type("K", (), {"parts": [_Part()]})()})()]

        def model_dump(self, **_kwargs: object) -> dict[str, object]:
            blob = b"\x89PNG" * 80
            return {
                "model_version": "gemini-nano-banana-2.1",
                "usage_metadata": {"total_token_count": 12},
                "candidates": [
                    {
                        "finish_reason": "STOP",
                        "content": {"parts": [{"inline_data": {"mime_type": "image/png", "data": blob}}]},
                    }
                ],
            }

    class _AioModels:
        async def generate_content(self, **kwargs):
            seen.update(kwargs)
            return _Response()

    class _Aio:
        models = _AioModels()

    class _Client:
        aio = _Aio()

    monkeypatch.setattr(gapi.GeminiImageAPI, "_build_client", lambda self: _Client())
    api = gapi.GeminiImageAPI()

    async def _run() -> tuple[bytes, dict[str, object], dict[str, object]]:
        from RH_ComfyUI.core.telemetry.wire_capture import get_vendor_raw, get_wire_audit

        ref = b"\x89PNG\r\n\x1a\n" + b"x" * 20
        image = await api.generate(
            model="gemini-nano-banana-2.1",
            prompt="cat",
            images=[ref],
            aspect_ratio="1:8",
            image_size="4K",
            thinking_level="high",
        )
        wire = get_wire_audit().get("request")
        body = wire if isinstance(wire, dict) else {}
        return image, get_vendor_raw(), body

    data, vendor, body = asyncio.run(_run())
    assert data == b"\x89PNG"
    assert body["num_images"] == 1
    images = body["images"]
    assert isinstance(images, list)
    assert images == [{"mime_type": "image/png", "data": "<bytes len=28>"}]
    assert vendor["model_version"] == "gemini-nano-banana-2.1"
    usage = vendor["usage_metadata"]
    assert isinstance(usage, dict)
    assert usage["total_token_count"] == 12
    stored = json.dumps(vendor)
    assert "\\x89PNG" not in stored
    assert b"\x89PNG".hex() not in stored
    parts = vendor["candidates"]
    assert isinstance(parts, list)
    assert "<bytes len=" in stored
    assert seen["model"] == "gemini-nano-banana-2.1"
    config = seen["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert config.response_modalities == ["IMAGE"]
    assert config.image_config is not None
    assert config.image_config.aspect_ratio == "1:8"
    assert config.image_config.image_size == "4K"
    assert config.thinking_config is not None
    assert str(config.thinking_config.thinking_level).upper().endswith("HIGH")


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


def test_gemini_enabled_list_gates_named_channel(monkeypatch):
    import RH_ComfyUI.utils.backends.gemini_image.config as gcfg

    monkeypatch.setattr(gapi, "SERVICE_CONFIG", _FakeConfig({"Gemini_Image_apikey": "AIzaKEY"}))
    monkeypatch.setattr(gcfg, "_cfg", lambda key: [] if key == "Gemini_Enabled_Models" else None)
    ch = GeminiImageChannel(logical_model="banana2")
    assert asyncio.run(ch.check_available()) is False
    reason = asyncio.run(ch.unavailable_reason())
    assert "banana2" in reason

    monkeypatch.setattr(gcfg, "_cfg", lambda key: ["banana2"] if key == "Gemini_Enabled_Models" else None)
    assert asyncio.run(ch.check_available()) is True


def test_gemini_invoke_refuses_when_disabled(monkeypatch):
    import RH_ComfyUI.utils.backends.gemini_image.config as gcfg
    from RH_ComfyUI.core.base.errors import ChannelError
    from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest

    monkeypatch.setattr(gapi, "SERVICE_CONFIG", _FakeConfig({"Gemini_Image_apikey": "AIzaKEY"}))
    monkeypatch.setattr(gcfg, "_cfg", lambda key: [] if key == "Gemini_Enabled_Models" else None)
    ch = GeminiImageChannel(logical_model="banana2")
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat")
    try:
        asyncio.run(ch.invoke(request=req))
    except ChannelError as exc:
        assert exc.retryable is True
        assert "banana2" in str(exc)
    else:
        raise AssertionError("disabled Gemini channel must refuse invoke")


def test_banana_pro_keeps_compat_channel_when_gemini_disabled(monkeypatch):
    import RH_ComfyUI.utils.backends.gemini_image.config as gcfg
    from RH_ComfyUI.models.image.defs import BananaProDef

    monkeypatch.setattr(gcfg, "_cfg", lambda key: [] if key == "Gemini_Enabled_Models" else None)
    channel_registry.clear()
    pro = BananaProDef()
    names = [b.channel.name for b in pro.channel_bindings()]
    assert "gemini" in names
    assert "gpt-image-2" in names
    gemini = next(b.channel for b in pro.channel_bindings() if b.channel.name == "gemini")
    assert asyncio.run(gemini.check_available()) is False
