"""BLT 图片编辑映射函数"""

from __future__ import annotations

import io

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.blt.api import BLTAPI


async def banana2_edit_mapper(
    request: GenerationRequest,
    api: BLTAPI,
) -> GenerationResult:
    """Banana2 编辑的映射+执行"""
    image = await api.draw_image(
        model="gemini-3.1-flash-image-preview",
        prompt=request.prompt,
        aspect_ratio=None,
        image_list=request.images,
    )
    if isinstance(image, int):
        raise RuntimeError(f"Banana2 编辑失败，错误码: {image}")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )


async def banana_pro_edit_mapper(
    request: GenerationRequest,
    api: BLTAPI,
) -> GenerationResult:
    """Banana Pro 编辑的映射+执行"""
    image = await api.draw_image(
        model="nano-banana-2-2k",
        prompt=request.prompt,
        aspect_ratio=None,
        image_list=request.images,
    )
    if isinstance(image, int):
        raise RuntimeError(f"Banana Pro 编辑失败，错误码: {image}")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )
