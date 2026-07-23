"""core.media_host — 媒体外链化扩展点"""

from __future__ import annotations

import asyncio

import pytest

from RH_ComfyUI.core.media_host import (
    MediaPublishError,
    get_media_publisher,
    materialize,
    media_host_registry,
    set_media_publisher,
)


@pytest.fixture(autouse=True)
def _clean_publisher():
    media_host_registry.clear()
    yield
    media_host_registry.clear()


def test_materialize_returns_none_without_publisher():
    assert get_media_publisher() is None
    assert asyncio.run(materialize(b"IMG", "image/png")) is None


def test_materialize_uses_registered_publisher():
    async def _pub(data: bytes, mime: str = "image/png") -> str:
        assert data == b"IMG"
        assert mime == "image/png"
        return "https://cdn.example.com/x.png"

    set_media_publisher(_pub)
    assert get_media_publisher() is _pub
    assert asyncio.run(materialize(b"IMG")) == "https://cdn.example.com/x.png"


def test_materialize_wraps_foreign_errors():
    class ForeignPublishError(RuntimeError):
        pass

    async def _pub(data: bytes, mime: str = "image/png") -> str:
        raise ForeignPublishError("R2 502")

    set_media_publisher(_pub)
    with pytest.raises(MediaPublishError, match="R2 502"):
        asyncio.run(materialize(b"IMG"))


def test_set_none_clears_publisher():
    async def _pub(data: bytes, mime: str = "image/png") -> str:
        return "https://x"

    set_media_publisher(_pub)
    set_media_publisher(None)
    assert get_media_publisher() is None
    assert asyncio.run(materialize(b"IMG")) is None
