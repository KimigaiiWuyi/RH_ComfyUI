"""MiniMax 图生图映射函数"""

from __future__ import annotations

import io
from typing import Dict, List

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.minimax.api import MiniMaxAPI


async def minimax_image01_img2img_mapper(
    request: GenerationRequest,
    api: MiniMaxAPI,
) -> GenerationResult:
    """MiniMax image-01 图生图映射+执行

    使用 subject_reference 将参考图片传入 MiniMax API 进行图生图。
    要求 request.images 中至少包含一张图片。
    """
    if not request.images:
        raise RuntimeError("MiniMax 图生图需要至少一张参考图片")

    ratio = api._calculate_aspect_ratio(request.width, request.height)

    # 构建 subject_reference 列表
    subject_reference: List[Dict[str, str]] = []
    for img_bytes in request.images:
        data_url = api._encode_image_to_base64(img_bytes, mime="image/jpeg")
        subject_reference.append(
            {
                "type": "character",
                "image_file": data_url,
            }
        )

    images = await api.generate_image(
        prompt=request.prompt,
        model="image-01",
        aspect_ratio=ratio,
        n=1,
        prompt_optimizer=True,
        subject_reference=subject_reference,
    )

    if isinstance(images, int):
        raise RuntimeError(f"MiniMax 图生图失败，错误码: {images}")

    if not images:
        raise RuntimeError("MiniMax 图生图失败，未返回图片")

    img = images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )
