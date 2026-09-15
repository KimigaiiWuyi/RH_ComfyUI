"""Seedance 参考图短边放大 + 宽高比裁切。"""

from __future__ import annotations

import time
import asyncio
import threading
from io import BytesIO

from PIL import Image

from RH_ComfyUI.core.schema.types import MediaRef, MediaKind, ContentItem, ContentItemType
from RH_ComfyUI.models.video.defs import Seedance2Def, Seedance25Def, Seedance2FastDef, Seedance2MiniDef
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.image_process import (
    SEEDANCE_ASPECT_MAX,
    SEEDANCE_ASPECT_MIN,
    SEEDANCE_IMAGE_MIN_EDGE,
    SEEDANCE_ASPECT_OFFICIAL_MAX,
    SEEDANCE_ASPECT_OFFICIAL_MIN,
    run_image_prep,
    ensure_min_edge,
    image_mime_from_bytes,
    clear_image_prep_cache,
    crop_to_seedance_aspect,
    prepare_seedance_image_ref,
    prepare_seedance_image_bytes,
    prepare_seedance_image_bytes_async,
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
    px = img.getpixel((0, 0))
    assert isinstance(px, tuple) and len(px) >= 4
    assert px[3] < 255


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
    large_out = _open(out.images[1])
    assert large_out.size == (512, 512)
    assert image_mime_from_bytes(out.images[1]) == "image/jpeg"

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


def test_prepare_seedance_image_bytes_async_matches_sync():
    raw = _png(80, 80)
    clear_image_prep_cache()

    async def _run() -> tuple[bytes, str]:
        return await prepare_seedance_image_bytes_async(raw)

    out_async, info_async = asyncio.run(_run())
    out_sync, info_sync = prepare_seedance_image_bytes(raw)
    assert out_async == out_sync
    assert info_async == info_sync
    img = _open(out_async)
    assert img.size[0] >= SEEDANCE_IMAGE_MIN_EDGE
    assert img.size[1] >= SEEDANCE_IMAGE_MIN_EDGE


def test_prepare_seedance_image_bytes_async_cache_hit():
    raw = _jpeg(100, 50)
    clear_image_prep_cache()

    async def _run() -> tuple[bytes, bytes]:
        a, _ = await prepare_seedance_image_bytes_async(raw)
        b, _ = await prepare_seedance_image_bytes_async(raw)
        return a, b

    first, second = asyncio.run(_run())
    assert first == second
    assert first is second


def test_run_image_prep_lets_event_loop_tick():
    worker_names: list[str] = []

    def _sleep_on_pool(payload: bytes) -> bytes:
        worker_names.append(threading.current_thread().name)
        time.sleep(0.25)
        return payload

    async def _run() -> int:
        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        task = asyncio.create_task(_ticker())
        await run_image_prep(_sleep_on_pool, b"x")
        task.cancel()
        return ticks

    ticks = asyncio.run(_run())
    assert ticks >= 3
    assert worker_names
    assert worker_names[0].startswith("rh-img-prep")


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
