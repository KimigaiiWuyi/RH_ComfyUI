"""Seedance 参考视频 bytes 优先走 media_host,图片仍 data URL。"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.core.media_host import media_host_registry, set_media_publisher
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.utils.backends.seedance.providers.ark import ArkSeedanceProvider

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 16


def test_video_bytes_use_publisher_not_data_url():
    async def _pub(data: bytes, mime: str) -> str:
        assert data == MP4
        assert mime.startswith("video/")
        return "https://r2.example.com/ref.mp4"

    media_host_registry.clear()
    set_media_publisher(_pub)
    try:
        p = ArkSeedanceProvider(api_key="x")
        url = asyncio.run(p.materialize_media(MediaRef(kind=MediaKind.VIDEO, data=MP4, mime_type="video/mp4")))
        assert url == "https://r2.example.com/ref.mp4"
        assert url is not None and not url.startswith("data:")
    finally:
        media_host_registry.clear()


def test_image_bytes_still_data_url_without_using_publisher():
    called = {"n": 0}

    async def _pub(data: bytes, mime: str) -> str:
        del data, mime
        called["n"] += 1
        return "https://r2.example.com/should-not-use.png"

    media_host_registry.clear()
    set_media_publisher(_pub)
    try:
        p = ArkSeedanceProvider(api_key="x")
        url = asyncio.run(p.materialize_media(MediaRef(kind=MediaKind.IMAGE, data=PNG, mime_type="image/png")))
        assert url is not None and url.startswith("data:image/png;base64,")
        assert called["n"] == 0
    finally:
        media_host_registry.clear()
