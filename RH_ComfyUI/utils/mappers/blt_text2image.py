"""BLT 文生图映射函数"""

from __future__ import annotations

import io

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.blt.api import BLTAPI


def _calculate_aspect_ratio(w: int, h: int) -> str:
    """根据宽度和高度自动计算最接近的宽高比"""
    actual_ratio = w / h
    ratios = {
        "21:9": 21 / 9,
        "16:9": 16 / 9,
        "4:3": 4 / 3,
        "1:1": 1 / 1,
        "3:4": 3 / 4,
        "9:16": 9 / 16,
    }
    closest_ratio = min(ratios.keys(), key=lambda k: abs(ratios[k] - actual_ratio))
    return closest_ratio


async def banana2_mapper(
    request: GenerationRequest,
    api: BLTAPI,
) -> GenerationResult:
    """Banana2 的映射+执行"""
    ratio = _calculate_aspect_ratio(request.width, request.height)
    image = await api.draw_image(
        model="gemini-3.1-flash-image-preview",
        prompt=request.prompt,
        aspect_ratio=ratio,
    )
    if isinstance(image, int):
        raise RuntimeError(f"Banana2 生成失败，错误码: {image}")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )


async def banana_pro_mapper(
    request: GenerationRequest,
    api: BLTAPI,
) -> GenerationResult:
    """Banana Pro 的映射+执行"""
    ratio = _calculate_aspect_ratio(request.width, request.height)
    image = await api.draw_image(
        model="nano-banana-2-2k",
        prompt=request.prompt,
        aspect_ratio=ratio,
    )
    if isinstance(image, int):
        raise RuntimeError(f"Banana Pro 生成失败，错误码: {image}")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )
