"""MiniMax 文生图映射函数"""

from __future__ import annotations

import asyncio

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..image_process import encode_pil_image
from ..backends.minimax.api import MiniMaxAPI


async def minimax_image01_mapper(
    request: GenerationRequest,
    api: MiniMaxAPI,
) -> GenerationResult:
    """MiniMax image-01 文生图映射+执行"""
    # ratio 端口是对外契约;旧调用方仍可只传宽高,由此换算兜底
    ratio = request.ratio or api._calculate_aspect_ratio(request.width, request.height)

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
    # PIL 编码是 CPU 重活，下线程免得冻事件循环（同 gpt_image2）
    data, mime = await asyncio.to_thread(encode_pil_image, img, "png")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=data,
        mime_type=mime,
    )
