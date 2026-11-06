"""gpt-image 家族:2.0 / 2.5 / 2.6 sunburst / flare 必须是五个独立 catalog 模型。"""

from __future__ import annotations

import io
import asyncio

import pytest
from PIL import Image, GifImagePlugin

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import NodeOutput
from RH_ComfyUI.models.image.defs import (
    ALL_MODELS,
    GptImage2Def,
    GptImage25FlareDef,
    GptImage26FlareDef,
    GptImage25SunburstDef,
    GptImage26SunburstDef,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.channels.channel import ProviderChannel
from RH_ComfyUI.core.channels.registry import channel_registry
from RH_ComfyUI.models.image.overrides import GptImageFamilyModel
from RH_ComfyUI.utils.mappers.gpt_image2 import gpt_image2_mapper
from RH_ComfyUI.utils.mappers.gpt_image2_params import (
    GPT_IMAGE2_QUALITIES,
    GPT_IMAGE25_QUALITIES,
    GPT_IMAGE_FAMILY_NAMES,
    GPT_IMAGE26_OUTPUT_FORMATS,
    gpt_image_family_inputs,
    resolve_gpt_image_family_model,
)

_FamilyCls = (
    type[GptImage2Def]
    | type[GptImage25SunburstDef]
    | type[GptImage25FlareDef]
    | type[GptImage26SunburstDef]
    | type[GptImage26FlareDef]
)
_FAMILY: tuple[_FamilyCls, ...] = (
    GptImage2Def,
    GptImage25SunburstDef,
    GptImage25FlareDef,
    GptImage26SunburstDef,
    GptImage26FlareDef,
)
_EXPECTED = (
    ("gpt-image-2", GptImage2Def, 76),
    ("gpt-image-2.5-sunburst", GptImage25SunburstDef, 77),
    ("gpt-image-2.5-flare", GptImage25FlareDef, 78),
    ("gpt-image-2.6-sunburst", GptImage26SunburstDef, 79),
    ("gpt-image-2.6-flare", GptImage26FlareDef, 80),
)


@pytest.mark.parametrize("name,cls,priority", _EXPECTED)
def test_family_catalog_identity(name: str, cls: _FamilyCls, priority: int):
    node = cls.node_def()
    assert node.name == name
    assert node.backend_model == name
    assert node.backend == "gpt-image-2"
    assert node.mapper_func is not None
    assert node.capabilities.priority == priority
    model = cls()
    assert model.name == name
    assert isinstance(model, GptImageFamilyModel)


def test_family_names_are_unique_and_in_all_models():
    names = [cls.node_def().name for cls in _FAMILY]
    assert names == list(GPT_IMAGE_FAMILY_NAMES)
    assert len(set(names)) == 5
    for cls in _FAMILY:
        assert cls in ALL_MODELS


def test_family_share_ports_not_generation_request_fields():
    ports = gpt_image_family_inputs()
    for cls in _FAMILY:
        node = cls.node_def()
        assert set(node.inputs) == set(ports)
        assert node.inputs["background"].values == ports["background"].values
        assert node.inputs["output_format"].default == "png"
        assert "width" not in node.inputs and "height" not in node.inputs
    assert GptImage2Def.node_def().inputs["output_format"].values == ["png", "jpeg"]
    for cls in (GptImage2Def, GptImage25SunburstDef, GptImage25FlareDef):
        assert cls.node_def().inputs["output_format"].values == ["png", "jpeg"]
    for cls in (GptImage26SunburstDef, GptImage26FlareDef):
        assert cls.node_def().inputs["output_format"].values == list(GPT_IMAGE26_OUTPUT_FORMATS)
    assert GptImage2Def.node_def().inputs["quality"].values == list(GPT_IMAGE2_QUALITIES)
    for cls in (GptImage25FlareDef, GptImage25SunburstDef, GptImage26SunburstDef, GptImage26FlareDef):
        assert cls.node_def().inputs["quality"].values == list(GPT_IMAGE25_QUALITIES)


def test_gpt_image2_rejects_xhigh_and_max():
    m = GptImage2Def()
    for q in ("xhigh", "max"):
        req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", params={"quality": q})
        with pytest.raises(ValidationError, match="quality"):
            m.validate(req)


@pytest.mark.parametrize(
    "cls",
    (GptImage25FlareDef, GptImage25SunburstDef, GptImage26FlareDef, GptImage26SunburstDef),
)
@pytest.mark.parametrize("quality", GPT_IMAGE25_QUALITIES)
def test_gpt_image25_accepts_five_qualities(cls: _FamilyCls, quality: str):
    cls().validate(GenerationRequest(task_type=TaskType.IMAGE, prompt="x", params={"quality": quality}))


@pytest.mark.parametrize("cls", (GptImage26SunburstDef, GptImage26FlareDef))
def test_gpt_image26_accepts_gif_output_format(cls: _FamilyCls):
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", params={"output_format": "gif"})
    cls().validate(req)
    assert req.params["output_format"] == "gif"


@pytest.mark.parametrize("cls", (GptImage2Def, GptImage25SunburstDef, GptImage25FlareDef))
def test_non_26_models_reject_gif_output_format(cls: _FamilyCls):
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", params={"output_format": "gif"})
    with pytest.raises(ValidationError, match="output_format"):
        cls().validate(req)


@pytest.mark.parametrize("cls", (GptImage26SunburstDef, GptImage26FlareDef))
def test_gpt_image26_transparent_gif_is_allowed(cls: _FamilyCls):
    """gif 支持透明,不命中 transparent+jpeg 拦截。"""
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="x",
        params={"background": "transparent", "output_format": "gif"},
    )
    cls().validate(req)


@pytest.mark.parametrize("cls", _FAMILY)
def test_family_rejects_transparent_jpeg(cls: _FamilyCls):
    m = cls()
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="x",
        params={"background": "transparent", "output_format": "jpeg"},
    )
    with pytest.raises(ValidationError, match="透明背景"):
        m.validate(req)


@pytest.mark.parametrize("cls", _FAMILY)
def test_family_validate_omitted_or_webp_becomes_png(cls: _FamilyCls):
    """image_pack 扩图不传 / 遗留 webp 必须过校验并落到 png。"""
    m = cls()
    omitted = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", ratio="16:9")
    m.validate(omitted)
    assert omitted.params["output_format"] == "png"
    leftover = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="x",
        ratio="16:9",
        params={"output_format": "webp"},
    )
    m.validate(leftover)
    assert leftover.params["output_format"] == "png"


@pytest.mark.parametrize("cls", _FAMILY)
def test_family_normalize_and_estimate_align(cls: _FamilyCls):
    from RH_ComfyUI.utils.mappers.gpt_image2_billing import estimate_gpt_image2_points

    m = cls()
    req = m.normalize(GenerationRequest(task_type=TaskType.IMAGE, prompt="x"))
    assert req.params["background"] == "auto"
    assert req.params["output_format"] == "png"
    priced = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="x",
        ratio="1:1",
        params={"quality": "high", "image_size": "4K"},
    )
    assert m.estimate_cost(priced) == estimate_gpt_image2_points("high", "1:1", "4K", model=m.name)
    assert m.point_range() == GptImage2Def().point_range()


def test_resolve_model_prefers_params_then_catalog():
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", model="gpt-image-2.5-flare")
    assert resolve_gpt_image_family_model(req) == "gpt-image-2.5-flare"
    req.params["model"] = "gpt-image-2.5-sunburst"
    assert resolve_gpt_image_family_model(req) == "gpt-image-2.5-sunburst"
    empty = GenerationRequest(task_type=TaskType.IMAGE, prompt="x")
    assert resolve_gpt_image_family_model(empty) == "gpt-image-2"


class _FakeGptApi:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def draw_image(
        self,
        model: str,
        prompt: str,
        aspect_ratio: str | None = "1:1",
        image_size: str | None = "2K",
        quality: str | None = "medium",
        background: str | None = "auto",
        output_format: str | None = "png",
        image_list: list[bytes] | None = None,
    ) -> Image.Image:
        self.calls.append({"model": model, "prompt": prompt, "image_size": image_size, "quality": quality})
        return Image.new("RGB", (2, 2), (1, 2, 3))


class _StubChannel(ProviderChannel):
    def __init__(self, name: str) -> None:
        self.name = name
        self.weight = 1

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: object) -> NodeOutput:
        raise NotImplementedError


@pytest.mark.parametrize("model_id", GPT_IMAGE_FAMILY_NAMES)
def test_mapper_sends_catalog_model_id(model_id: str):
    api = _FakeGptApi()
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="一只猫",
        model=model_id,
        ratio="1:1",
        params={"image_size": "1K", "quality": "low"},
    )
    asyncio.run(gpt_image2_mapper(req, api))
    assert len(api.calls) == 1
    assert api.calls[0]["model"] == model_id


def test_mapper_forwards_xhigh_quality():
    api = _FakeGptApi()
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="p",
        model="gpt-image-2.5-flare",
        params={"quality": "xhigh"},
    )
    asyncio.run(gpt_image2_mapper(req, api))
    assert api.calls[0]["quality"] == "xhigh"
    assert api.calls[0]["model"] == "gpt-image-2.5-flare"


def test_mapper_attaches_usage_from_draw_result():
    from RH_ComfyUI.utils.backends.gpt_image2.api import GPTImageDrawResult

    class _Api:
        async def draw_image(self, **kwargs: object) -> GPTImageDrawResult:
            return GPTImageDrawResult(
                image=Image.new("RGB", (2, 2), (1, 2, 3)),
                usage={"vendor_unit": "tokens", "output_tokens": 50, "raw_usage": {"output_tokens": 50}},
                raw={"usage": {"output_tokens": 50}},
            )

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="p", model="gpt-image-2")
    out = asyncio.run(gpt_image2_mapper(req, _Api()))
    assert out.usage["output_tokens"] == 50
    assert out.raw["usage"]["output_tokens"] == 50


def _make_animated_gif(frames: int = 3) -> bytes:
    imgs = [Image.new("P", (4, 4)) for _ in range(frames)]
    for i, img in enumerate(imgs):
        img.putpalette([i * 60, 0, 0] * 256)
    buf = io.BytesIO()
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:], duration=100, loop=0)
    return buf.getvalue()


def test_mapper_passthrough_gif_bytes_not_flattened():
    """draw_result.data(GIF 字节)必须原样进 NodeOutput,不经 PIL 重编。"""
    from RH_ComfyUI.utils.backends.gpt_image2.api import GPTImageDrawResult

    gif_bytes = _make_animated_gif()

    class _Api:
        async def draw_image(self, **kwargs: object) -> GPTImageDrawResult:
            return GPTImageDrawResult(data=gif_bytes, mime="image/gif", usage={}, raw={})

    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="p",
        model="gpt-image-2.6-flare",
        params={"output_format": "gif"},
    )
    out = asyncio.run(gpt_image2_mapper(req, _Api()))
    assert out.mime_type == "image/gif"
    assert out.data == gif_bytes
    assert out.outputs["image"] == gif_bytes
    with Image.open(io.BytesIO(out.data)) as img:
        assert isinstance(img, GifImagePlugin.GifImageFile)
        assert img.n_frames == 3


def test_encode_image_bytes_preserves_animated_gif():
    """encode_image_bytes(raw, 'gif') 对已是 GIF 的字节整段透传。"""
    from RH_ComfyUI.utils.image_process import encode_image_bytes

    gif_bytes = _make_animated_gif()
    assert encode_image_bytes(gif_bytes, "gif") == gif_bytes
    png_still = Image.new("RGB", (4, 4), (1, 2, 3))
    buf = io.BytesIO()
    png_still.save(buf, format="PNG")
    converted = encode_image_bytes(buf.getvalue(), "gif")
    assert converted[:4] == b"GIF8"


def test_mime_for_output_format_gif():
    from RH_ComfyUI.utils.mappers.gpt_image2_params import mime_for_output_format

    assert mime_for_output_format("gif") == "image/gif"


def test_mapper_does_not_collapse_25_to_gpt_image_2():
    api = _FakeGptApi()
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="p",
        model="gpt-image-2.5-sunburst",
    )
    asyncio.run(gpt_image2_mapper(req, api))
    assert api.calls[0]["model"] == "gpt-image-2.5-sunburst"
    assert api.calls[0]["model"] != "gpt-image-2"


def test_mapper_error_uses_catalog_model_not_gpt_image_2_label():
    class _FailApi:
        async def draw_image(self, **kwargs: object) -> int:
            return 404

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="p", model="gpt-image-2.5-flare")
    with pytest.raises(RuntimeError, match="gpt-image-2.5-flare 生成失败") as ei:
        asyncio.run(gpt_image2_mapper(req, _FailApi()))
    assert "GPT-Image2" not in str(ei.value)


def test_channel_bindings_do_not_leak_across_family():
    channel_registry.clear()
    try:
        azure = _StubChannel("azure")
        channel_registry.register_binding("gpt-image-2", azure, vendor_model="my-deploy")
        gpt_names = [b.channel.name for b in GptImage2Def().channel_bindings()]
        sun_names = [b.channel.name for b in GptImage25SunburstDef().channel_bindings()]
        flare_names = [b.channel.name for b in GptImage25FlareDef().channel_bindings()]
        sun26_names = [b.channel.name for b in GptImage26SunburstDef().channel_bindings()]
        flare26_names = [b.channel.name for b in GptImage26FlareDef().channel_bindings()]
        assert "azure" in gpt_names
        assert "azure" not in sun_names
        assert "azure" not in flare_names
        assert "azure" not in sun26_names
        assert "azure" not in flare26_names
        assert sun_names == ["gpt-image-2"]
        sun_vendor = [b.vendor_model for b in GptImage25SunburstDef().channel_bindings()]
        flare_vendor = [b.vendor_model for b in GptImage25FlareDef().channel_bindings()]
        sun26_vendor = [b.vendor_model for b in GptImage26SunburstDef().channel_bindings()]
        flare26_vendor = [b.vendor_model for b in GptImage26FlareDef().channel_bindings()]
        gpt_vendor = [b.vendor_model for b in GptImage2Def().channel_bindings() if b.channel.name == "gpt-image-2"]
        assert sun_vendor == ["gpt-image-2.5-sunburst"]
        assert flare_vendor == ["gpt-image-2.5-flare"]
        assert sun26_vendor == ["gpt-image-2.6-sunburst"]
        assert flare26_vendor == ["gpt-image-2.6-flare"]
        assert gpt_vendor == ["gpt-image-2"]
    finally:
        channel_registry.clear()


def test_gpt_image2_api_forwards_xhigh_and_max():
    from RH_ComfyUI.utils.backends.gpt_image2.api import GPTImage2API

    api = GPTImage2API()
    captured: dict[str, object] = {}

    async def fake_request(
        method: str,
        url: str,
        headers: object = None,
        json: object = None,
        data: object = None,
    ) -> int:
        captured["json"] = json
        return 500

    setattr(api, "_request", fake_request)
    asyncio.run(api.draw_image(model="gpt-image-2.5-flare", prompt="x", quality="xhigh"))
    body = captured["json"]
    assert isinstance(body, dict) and body["quality"] == "xhigh"
    asyncio.run(api.draw_image(model="gpt-image-2.5-flare", prompt="x", quality="max"))
    body = captured["json"]
    assert isinstance(body, dict) and body["quality"] == "max"
    asyncio.run(api.draw_image(model="gpt-image-2", prompt="x", quality="ultra"))
    body = captured["json"]
    assert isinstance(body, dict) and "quality" not in body


def test_gpt_image2_api_forwards_gif_output_format():
    """output_format=gif 必须透传到 wire body(2.6 直出动图)。"""
    from RH_ComfyUI.utils.backends.gpt_image2.api import GPTImage2API

    api = GPTImage2API()
    captured: dict[str, object] = {}

    async def fake_request(
        method: str,
        url: str,
        headers: object = None,
        json: object = None,
        data: object = None,
    ) -> int:
        captured["json"] = json
        return 500

    setattr(api, "_request", fake_request)
    asyncio.run(api.draw_image(model="gpt-image-2.6-flare", prompt="x", output_format="gif"))
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["model"] == "gpt-image-2.6-flare"
    assert body["output_format"] == "gif"
