"""上传前参考图必须是标准 JPEG;透明通道铺中性灰底。"""

from __future__ import annotations

import io

from PIL import Image

from RH_ComfyUI.core.base.image import ImageGenerationBase
from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.types import (
    MediaRef,
    PortSpec,
    PortType,
    MediaKind,
    NodeOutput,
    ContentItem,
    ContentItemType,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.image_process import (
    is_standard_jpeg,
    ensure_standard_image,
    image_mime_from_bytes,
    standardize_generation_images,
)
from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding
from RH_ComfyUI.utils.backends.gemini_image.api import _inline_image_part


def _encode(mode: str, size: tuple[int, int], fmt: str, **save_kw: object) -> bytes:
    color: object
    if mode == "RGBA":
        color = (255, 0, 0, 80)
    else:
        color = (10, 20, 30)
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **save_kw)
    return buf.getvalue()


def test_jpeg_pass_through_png_becomes_jpeg() -> None:
    jpeg = _encode("RGB", (12, 10), "JPEG", quality=90)
    png = _encode("RGB", (12, 10), "PNG")
    out_j, mime_j = ensure_standard_image(jpeg)
    out_p, mime_p = ensure_standard_image(png)
    assert out_j is jpeg and mime_j == "image/jpeg"
    assert mime_p == "image/jpeg" and out_p.startswith(b"\xff\xd8\xff")
    assert is_standard_jpeg(jpeg) and is_standard_jpeg(out_p)


def test_webp_becomes_jpeg() -> None:
    webp = _encode("RGB", (16, 16), "WEBP")
    assert not is_standard_jpeg(webp)
    out, mime = ensure_standard_image(webp)
    assert mime == "image/jpeg"
    assert out.startswith(b"\xff\xd8\xff")
    assert Image.open(io.BytesIO(out)).size == (16, 16)


def test_transparent_png_flattens_to_gray_jpeg() -> None:
    """全透明像素应变成中性灰,而不是白/黑或残留 alpha。"""
    img = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    out, mime = ensure_standard_image(buf.getvalue())
    assert mime == "image/jpeg"
    decoded = Image.open(io.BytesIO(out))
    assert decoded.mode == "RGB"
    px = decoded.getpixel((0, 0))
    assert all(abs(int(c) - 128) <= 8 for c in px)


def test_transparent_webp_flattens_to_gray_jpeg() -> None:
    webp = _encode("RGBA", (8, 8), "WEBP")
    out, mime = ensure_standard_image(webp)
    assert mime == "image/jpeg"
    decoded = Image.open(io.BytesIO(out))
    assert decoded.mode == "RGB"
    assert decoded.getpixel((0, 0)) != (255, 0, 0)


def test_gif_becomes_jpeg() -> None:
    gif = _encode("RGB", (10, 10), "GIF")
    out, mime = ensure_standard_image(gif)
    assert mime == "image/jpeg"
    assert is_standard_jpeg(out)


def test_garbage_bytes_fail_open() -> None:
    raw = b"not-an-image"
    out, mime = ensure_standard_image(raw)
    assert out is raw
    assert mime == "image/png"


def test_standardize_rewrites_flat_and_ordered_content() -> None:
    webp = _encode("RGB", (20, 20), "WEBP")
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="p",
        images=[webp],
        ordered_content=[
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, data=webp, mime_type="image/webp"),
            )
        ],
    )
    out = standardize_generation_images(req)
    assert image_mime_from_bytes(out.images[0]) == "image/jpeg"
    media = out.ordered_content[0].media
    assert media is not None and media.mime_type == "image/jpeg"
    assert is_standard_jpeg(media.data or b"")


class _MiniImage(ImageGenerationBase):
    name = "mini_img"
    display_name = "mini_img"
    card = ModelCard(description="x")
    supports_edit = True
    max_input_images = 4

    def input_schema(self) -> dict[str, PortSpec]:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(LocalChannel("local"))]

    async def execute_on_channel(self, request, binding, *, on_progress=None) -> NodeOutput:
        return NodeOutput()


def test_image_normalize_converts_webp() -> None:
    webp = _encode("RGB", (14, 14), "WEBP")
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", images=[webp])
    out = _MiniImage().normalize(req)
    assert image_mime_from_bytes(out.images[0]) == "image/jpeg"


def test_video_normalize_converts_small_webp() -> None:
    from RH_ComfyUI.core.base.video import VideoGenerationBase

    class _MiniVideo(VideoGenerationBase):
        name = "mini_vid"
        display_name = "mini_vid"
        card = ModelCard(description="x")

        def input_schema(self) -> dict[str, PortSpec]:
            return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

        def channel_bindings(self) -> list[ChannelBinding]:
            return [ChannelBinding(LocalChannel("local"))]

        async def execute_on_channel(self, request, binding, *, on_progress=None) -> NodeOutput:
            return NodeOutput()

    webp = _encode("RGB", (40, 40), "WEBP")
    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="动", images=[webp])
    out = _MiniVideo().normalize(req)
    assert image_mime_from_bytes(out.images[0]) == "image/jpeg"


def test_gemini_inline_part_uses_real_mime() -> None:
    jpeg = _encode("RGB", (8, 8), "JPEG", quality=85)
    png = _encode("RGB", (8, 8), "PNG")
    assert _inline_image_part(jpeg)["mime_type"] == "image/jpeg"
    assert _inline_image_part(png)["mime_type"] == "image/png"
