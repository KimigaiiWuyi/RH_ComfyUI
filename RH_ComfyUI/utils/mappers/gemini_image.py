"""Gemini 生图映射函数 — 自适应文生图 / 图生图(编辑)"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.types import NodeOutput
from ..core.request import GenerationRequest

if TYPE_CHECKING:
    from ..backends.gemini_image.api import GeminiImageAPI


async def gemini_flash_image_mapper(
    request: GenerationRequest,
    api: "GeminiImageAPI",
) -> NodeOutput:
    """有图走图生图(编辑),无图走文生图。Gemini 只吃 aspect_ratio + image_size。"""
    model = request.params.get("model") or "gemini-3.1-flash-image-preview"
    ratio = request.ratio or "1:1"
    image_size = str(request.params.get("image_size") or "2K")

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
