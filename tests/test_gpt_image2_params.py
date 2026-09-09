"""gpt-image-2 catalog 档位解析 + 产物转码。"""

from __future__ import annotations

import io

from PIL import Image

from RH_ComfyUI.utils.image_process import encode_pil_image, encode_image_bytes, image_mime_from_bytes
from RH_ComfyUI.utils.mappers.gpt_image2_params import (
    mime_for_output_format,
    gpt_image2_transparent_jpeg,
    resolve_gpt_image2_background,
    resolve_gpt_image2_output_format,
)


def _rgb_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_resolve_defaults_and_aliases():
    assert resolve_gpt_image2_background(None) == "auto"
    assert resolve_gpt_image2_output_format(None) == "png"
    assert resolve_gpt_image2_output_format({"output_format": "JPG"}) == "jpeg"
    assert resolve_gpt_image2_output_format({"output_format": "webp"}) == "png"
    assert resolve_gpt_image2_background({"background": "TRANSPARENT"}) == "transparent"


def test_transparent_jpeg_combo():
    assert gpt_image2_transparent_jpeg({"background": "transparent", "output_format": "jpeg"}) is True
    assert gpt_image2_transparent_jpeg({"background": "transparent", "output_format": "png"}) is False
    assert gpt_image2_transparent_jpeg({}) is False


def test_encode_png_to_jpeg_and_webp():
    png = _rgb_png()
    jpeg = encode_image_bytes(png, "jpeg")
    assert jpeg[:3] == b"\xff\xd8\xff"
    webp = encode_image_bytes(png, "webp")
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP"
    assert encode_image_bytes(png, "png")[:8] == b"\x89PNG\r\n\x1a\n"


def test_encode_pil_and_mime():
    img = Image.new("RGB", (2, 2), (1, 2, 3))
    data, mime = encode_pil_image(img, "webp")
    assert mime == "image/webp"
    assert data[:4] == b"RIFF"
    assert mime_for_output_format("jpeg") == "image/jpeg"
    assert image_mime_from_bytes(data) == "image/webp"
