"""Gemini 生图映射函数 — 自适应文生图 / 图生图(编辑)"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..core.types import NodeOutput
from ..core.request import GenerationRequest

if TYPE_CHECKING:
    from ..backends.gemini_image.api import GeminiImageAPI


def _default_image_size(model: str) -> Optional[str]:
    """尺寸档默认值:一代(gemini-2.5-flash-image)不支持 image_config.image_size,
    必须整个字段不发;3.x 系(banana2 flash / banana_pro)保持既有默认 2K。"""
    return None if "2.5" in model else "2K"


def _gemini_ratio(request: GenerationRequest) -> str:
    """Gemini 只认具体宽高比;banana_pro 等 schema 默认 auto 时回落 1:1。"""
    ratio = (request.ratio or "").strip()
    if not ratio or ratio.lower() == "auto":
        return "1:1"
    return ratio


async def gemini_flash_image_mapper(
    request: GenerationRequest,
    api: "GeminiImageAPI",
) -> NodeOutput:
    """有图走图生图(编辑),无图走文生图。Gemini 只吃 aspect_ratio + image_size。"""
    model = request.params.get("model") or "gemini-3.1-flash-image-preview"
    ratio = _gemini_ratio(request)
    raw_size = request.params.get("image_size")
    image_size = str(raw_size) if raw_size else _default_image_size(model)

    data = await api.generate(
        model=model,
        prompt=request.prompt,
        images=request.images or None,
        aspect_ratio=ratio,
        image_size=image_size,
    )
    return NodeOutput(
        status="ok",
        output_type="image",
        data=data,
        mime_type="image/png",
        outputs={"image": data},
    )
