"""MiniMax 图生图映射函数

通过 MiniMax image-01 的 subject_reference 参数实现图生图:
用户上传参考人物照片,模型保持人物一致性生成新图。
"""

from __future__ import annotations

import io

from ..core.request import OutputType, GenerationResult, GenerationRequest
from ..backends.minimax.api import MiniMaxAPI


async def minimax_image01_img2img_mapper(
    request: GenerationRequest,
    api: MiniMaxAPI,
) -> GenerationResult:
    """MiniMax image-01 图生图映射+执行

    将用户上传的参考图片编码为 base64 data URL,
    通过 subject_reference 参数传给 MiniMax API。
    """
    ratio = api._calculate_aspect_ratio(request.width, request.height)

    # 构建 subject_reference:将用户上传的图片编码为 base64
    subject_reference = []
    for img_bytes in request.images:
        data_url = api._encode_image_to_base64(img_bytes)
        subject_reference.append({"image": data_url})

    images = await api.generate_image(
        prompt=request.prompt,
        model="image-01",
        aspect_ratio=ratio,
        n=1,
        prompt_optimizer=True,
        subject_reference=subject_reference or None,
    )

    if isinstance(images, int):
        raise RuntimeError(f"MiniMax image-01 图生图失败，错误码: {images}")

    if not images:
        raise RuntimeError("MiniMax image-01 图生图失败，未返回图片")

    img = images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return GenerationResult(
        output_type=OutputType.IMAGE,
        data=buf.getvalue(),
        mime_type="image/png",
    )
