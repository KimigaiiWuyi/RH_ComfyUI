"""RH_ComfyUI.api._decode_media_bytes — 媒体 URL 防御与 data URI 解码。"""

from __future__ import annotations

import base64
import asyncio

import pytest

from RH_ComfyUI.api import _decode_data_uri, _preview_media_url, _decode_media_bytes


def test_preview_media_url_redacts_data_uri():
    raw = base64.b64encode(b"PNGDATA").decode()
    uri = f"data:image/png;base64,{raw}"
    preview = _preview_media_url(uri)
    assert "PNGDATA" not in preview
    assert "data:image/png;base64," in preview
    assert "payload" in preview


def test_decode_data_uri_base64():
    payload = base64.b64encode(b"hello-bytes").decode()
    assert _decode_data_uri(f"data:image/png;base64,{payload}") == b"hello-bytes"


def test_decode_media_bytes_prefers_data_base64():
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    b64 = base64.b64encode(raw).decode()
    out = asyncio.run(_decode_media_bytes({"data_base64": b64, "url": "/should/not/fetch"}))
    assert out == raw


def test_decode_media_bytes_accepts_data_uri_as_url():
    raw = b"\xff\xd8\xff" + b"JPEG"
    b64 = base64.b64encode(raw).decode()
    out = asyncio.run(_decode_media_bytes({"url": f"data:image/jpeg;base64,{b64}"}))
    assert out == raw


def test_decode_media_bytes_rejects_relative_url():
    with pytest.raises(ValueError, match="http\\(s\\)|不可下载"):
        asyncio.run(_decode_media_bytes({"url": "/api/host/assets/x/raw"}))


def test_decode_media_bytes_rejects_asset_scheme():
    with pytest.raises(ValueError, match="不可下载|http"):
        asyncio.run(_decode_media_bytes({"url": "asset://ark-asset-id"}))


def test_decode_media_bytes_rejects_empty_url():
    with pytest.raises(ValueError, match="缺少|为空"):
        asyncio.run(_decode_media_bytes({"url": "   "}))
