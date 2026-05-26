"""MiniMax 文生图映射函数"""

from __future__ import annotations

import io

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.minimax.api import MiniMaxAPI


async def minimax_image01_mapper(
    request: GenerationRequest,
    api: MiniMaxAPI,
) -> GenerationResult:
    """MiniMax image-01 文生图映射+执行"""
    ratio = api._calculate_aspect_ratio(request.width, request.height)

    images = await api.generate_image(
        prompt=request.prompt,
        model="image-01",
        aspect_ratio=ratio,
        n=1,
        prompt_optimizer=True,
    )

    if isinstance(images, int):
        raise RuntimeError(f"MiniMax image-01 生成失败，错误码: {images}")

    if not images:
        raise RuntimeError("MiniMax image-01 生成失败，未返回图片")

    img = images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )
