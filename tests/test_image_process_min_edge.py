"""Seedance 参考图短边放大 + 宽高比裁切。"""

from __future__ import annotations

import asyncio
from io import BytesIO

from PIL import Image

from RH_ComfyUI.core.schema.request import GenerationRequest, TaskType
from RH_ComfyUI.core.schema.types import ContentItem, ContentItemType, MediaKind, MediaRef
from RH_ComfyUI.models.video.defs import Seedance2Def, Seedance2FastDef, Seedance2MiniDef, Seedance25Def
from RH_ComfyUI.utils.image_process import (
    SEEDANCE_ASPECT_MAX,
    SEEDANCE_ASPECT_MIN,
    SEEDANCE_ASPECT_OFFICIAL_MAX,
    SEEDANCE_ASPECT_OFFICIAL_MIN,
    SEEDANCE_IMAGE_MIN_EDGE,
    crop_to_seedance_aspect,
    ensure_min_edge,
    image_mime_from_bytes,
    prepare_seedance_image_bytes,
    prepare_seedance_image_ref,
)


def _png(w: int, h: int, *, mode: str = "RGB", color=None) -> bytes:
    if color is None:
        color = (0, 128, 255, 128) if mode == "RGBA" else (0, 128, 255)
    img = Image.new(mode, (w, h), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (20, 180, 90))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    img = Image.open(BytesIO(data))
    img.load()
    return img


def test_small_jpeg_upscales_both_sides_keep_rgb():
    raw = _jpeg(100, 50)
    out, info = ensure_min_edge(raw)
    assert info
    img = _open(out)
    assert img.size[0] >= SEEDANCE_IMAGE_MIN_EDGE
    assert img.size[1] >= SEEDANCE_IMAGE_MIN_EDGE
    assert img.mode == "RGB"
    assert image_mime_from_bytes(out) == "image/jpeg"
    # 100×50 → scale=6 → 600×300
    assert img.size == (600, 300)


def test_narrow_png_upscales_keep_rgb():
    raw = _png(400, 200)
    out, info = ensure_min_edge(raw)
    assert info
    img = _open(out)
    assert img.mode == "RGB"
    assert image_mime_from_bytes(out) == "image/png"
    # 400×200 → scale=1.5 → 600×300
    assert img.size == (600, 300)


def test_rgba_png_keeps_alpha():
    raw = _png(80, 80, mode="RGBA", color=(255, 0, 0, 90))
    out, info = ensure_min_edge(raw)
    assert info
    img = _open(out)
    assert img.mode == "RGBA"
    assert image_mime_from_bytes(out) == "image/png"
    assert img.size == (300, 300)
    # 抽样确认 alpha 没被拍成不透明
    assert img.getpixel((0, 0))[3] < 255


def test_already_large_returns_original():
    raw = _jpeg(640, 480)
    out, info = ensure_min_edge(raw)
    assert info == ""
    assert out is raw


def test_exactly_300_returns_original():
    raw = _png(300, 300)
    out, info = ensure_min_edge(raw)
    assert info == ""
    assert out is raw


def test_corrupt_bytes_passthrough():
    raw = b"not-an-image"
    out, info = ensure_min_edge(raw)
    assert out == raw
    assert info == ""


def test_prepare_request_upscales_flat_and_ordered_images():
    small = _jpeg(96, 64)
    large = _png(512, 512)
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="跑起来",
        images=[small, large],
        ordered_content=[
            ContentItem(type=ContentItemType.TEXT, text="跑起来"),
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, data=small),
            ),
        ],
    )
    out = asyncio.run(Seedance2Def().prepare_request(req))

    assert len(out.images) == 2
    up_flat = _open(out.images[0])
    assert up_flat.size[0] >= 300 and up_flat.size[1] >= 300
    assert out.images[1] == large

    oc_img = next(i for i in out.ordered_content if i.type == ContentItemType.IMAGE)
    assert oc_img.media is not None
    assert oc_img.media.data is not None
    assert oc_img.media.url is None
    up_oc = _open(oc_img.media.data)
    assert up_oc.size[0] >= 300 and up_oc.size[1] >= 300


def test_prepare_request_skips_ark_asset_url():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="人像",
        ordered_content=[
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, url="asset://ark-face-1"),
            ),
        ],
    )
    out = asyncio.run(Seedance2Def().prepare_request(req))
    media = out.ordered_content[0].media
    assert media is not None
    assert media.url == "asset://ark-face-1"
    assert media.data is None


def test_prepare_request_upscales_on_seedance20_fast_and_mini():
    """2.0 Fast / Mini 与 seedance2 共用 SeedanceVideoModel.prepare_request。"""
    small = _jpeg(80, 120)
    for cls in (Seedance2FastDef, Seedance2MiniDef):
        req = GenerationRequest(task_type=TaskType.VIDEO, prompt="动起来", images=[small])
        out = asyncio.run(cls().prepare_request(req))
        img = _open(out.images[0])
        assert img.size[0] >= 300 and img.size[1] >= 300, cls.__name__


def test_prepare_seedance_image_ref_clears_url_after_upscale():
    small = _png(64, 64)
    ref = MediaRef(kind=MediaKind.IMAGE, data=small, url="https://cdn.example.com/tiny.png")
    out = asyncio.run(prepare_seedance_image_ref(ref))
    assert out.url is None
    assert out.data is not None
    img = _open(out.data)
    assert img.size == (300, 300)


def _assert_seedance_aspect_ok(w: int, h: int) -> None:
    ar = w / h
    assert SEEDANCE_ASPECT_OFFICIAL_MIN <= ar <= SEEDANCE_ASPECT_OFFICIAL_MAX
    assert ar <= SEEDANCE_ASPECT_MAX + 1e-9
    assert ar >= SEEDANCE_ASPECT_MIN - 1e-9


def test_wide_jpeg_crops_to_max_aspect():
    """复现网关 400:2.69 超 2.50,应居中裁到 2.49。"""
    raw = _jpeg(2690, 1000)
    out, info = crop_to_seedance_aspect(raw)
    assert info
    img = _open(out)
    assert img.size[1] == 1000
    assert img.size[0] < 2690
    _assert_seedance_aspect_ok(*img.size)
    assert abs(img.size[0] / img.size[1] - SEEDANCE_ASPECT_MAX) < 0.02


def test_tall_png_crops_to_min_aspect():
    raw = _png(1000, 2690)
    out, info = crop_to_seedance_aspect(raw)
    assert info
    img = _open(out)
    assert img.size[0] == 1000
    assert img.size[1] < 2690
    _assert_seedance_aspect_ok(*img.size)
    assert abs(img.size[0] / img.size[1] - SEEDANCE_ASPECT_MIN) < 0.02


def test_valid_aspect_returns_original():
    raw = _jpeg(640, 480)
    out, info = crop_to_seedance_aspect(raw)
    assert info == ""
    assert out is raw


def test_small_wide_upscales_then_crops():
    """100×50 先放大到 600×300(AR=2.00,合法),不应再裁。"""
    raw = _jpeg(100, 50)
    out, info = prepare_seedance_image_bytes(raw)
    assert info
    img = _open(out)
    assert img.size[0] >= SEEDANCE_IMAGE_MIN_EDGE
    assert img.size[1] >= SEEDANCE_IMAGE_MIN_EDGE
    _assert_seedance_aspect_ok(*img.size)


def test_small_and_too_wide_upscales_then_crops():
    """269×100 = 2.69,放大后仍超限,再裁到 2.49。"""
    raw = _jpeg(269, 100)
    out, info = prepare_seedance_image_bytes(raw)
    assert "aspect" in info
    img = _open(out)
    assert img.size[0] >= SEEDANCE_IMAGE_MIN_EDGE
    assert img.size[1] >= SEEDANCE_IMAGE_MIN_EDGE
    _assert_seedance_aspect_ok(*img.size)
    assert abs(img.size[0] / img.size[1] - SEEDANCE_ASPECT_MAX) < 0.02


def test_prepare_request_crops_wide_flat_and_ordered():
    wide = _jpeg(2690, 1000)
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="跑起来",
        images=[wide],
        ordered_content=[
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, data=wide, url="https://cdn.example.com/wide.jpg"),
            ),
        ],
    )
    out = asyncio.run(Seedance2Def().prepare_request(req))
    flat = _open(out.images[0])
    _assert_seedance_aspect_ok(*flat.size)
    oc_img = next(i for i in out.ordered_content if i.type == ContentItemType.IMAGE)
    assert oc_img.media is not None
    assert oc_img.media.url is None
    assert oc_img.media.data is not None
    _assert_seedance_aspect_ok(*_open(oc_img.media.data).size)


def test_prepare_request_crops_on_seedance25():
    wide = _jpeg(2690, 1000)
    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="动起来", images=[wide])
    out = asyncio.run(Seedance25Def().prepare_request(req))
    img = _open(out.images[0])
    _assert_seedance_aspect_ok(*img.size)


def test_prepare_seedance_image_ref_clears_url_after_crop():
    wide = _png(2690, 1000)
    ref = MediaRef(kind=MediaKind.IMAGE, data=wide, url="https://cdn.example.com/wide.png")
    out = asyncio.run(prepare_seedance_image_ref(ref))
    assert out.url is None
    assert out.data is not None
    _assert_seedance_aspect_ok(*_open(out.data).size)


def test_prepare_request_clamps_audio_and_inlines(monkeypatch):
    raw_audio = b"FAKEAUDIO" * 32
    trimmed = b"TRIMMEDAUDIO" * 16

    async def _fake_audio(data, **kwargs):
        assert data == raw_audio
        return trimmed, 15.0, "trim"

    monkeypatch.setattr(
        "RH_ComfyUI.models.video.overrides.clamp_seedance_ref_audio",
        _fake_audio,
        raising=False,
    )
    # 函数是 prepare_request 内 import 的,补 patch 模块路径
    import RH_ComfyUI.utils.audio_process as audio_mod

    monkeypatch.setattr(audio_mod, "clamp_seedance_ref_audio", _fake_audio)

    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="配乐",
        audio_refs=[MediaRef(kind=MediaKind.AUDIO, data=raw_audio, url="https://cdn.example.com/long.m4a")],
        ordered_content=[
            ContentItem(
                type=ContentItemType.AUDIO,
                media=MediaRef(kind=MediaKind.AUDIO, data=raw_audio, url="https://cdn.example.com/long.m4a"),
            ),
        ],
    )
    out = asyncio.run(Seedance2Def().prepare_request(req))
    assert out.audio_refs[0].data == trimmed
    assert out.audio_refs[0].url is None
    oc = out.ordered_content[0]
    assert oc.media is not None
    assert oc.media.data == trimmed
    assert oc.media.url is None


def test_prepare_request_seedance25_video_uses_30s_max(monkeypatch):
    seen: dict[str, float] = {}

    async def _fake_video(data, **kwargs):
        seen["max_s"] = kwargs.get("max_s", 0)
        seen["min_pixels"] = kwargs.get("min_pixels", 0)
        return data, 18.0, None

    import RH_ComfyUI.utils.video_process as video_mod

    monkeypatch.setattr(video_mod, "prepare_seedance_ref_video", _fake_video)

    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="长镜头",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, data=b"VID" * 16)],
    )
    asyncio.run(Seedance25Def().prepare_request(req))
    assert seen["max_s"] == 30.0
    assert seen["min_pixels"] == 407696

