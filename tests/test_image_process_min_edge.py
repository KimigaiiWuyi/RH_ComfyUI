"""Seedance 参考图短边放大:宽高均须 ≥300,保持 RGB/RGBA。"""

from __future__ import annotations

import asyncio
from io import BytesIO

from PIL import Image

from RH_ComfyUI.core.schema.request import GenerationRequest, TaskType
from RH_ComfyUI.core.schema.types import ContentItem, ContentItemType, MediaKind, MediaRef
from RH_ComfyUI.models.video.defs import Seedance2Def, Seedance2FastDef, Seedance2MiniDef
from RH_ComfyUI.utils.image_process import (
    SEEDANCE_IMAGE_MIN_EDGE,
    ensure_min_edge,
    image_mime_from_bytes,
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
