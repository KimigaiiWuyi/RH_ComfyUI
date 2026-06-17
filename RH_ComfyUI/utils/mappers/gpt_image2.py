"""GPT-Image2 生图模型自适应映射函数"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ..core.types import NodeOutput
from ..core.request import GenerationRequest

if TYPE_CHECKING:
    from ..backends.gpt_image2.api import GPTImage2API


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


async def gpt_image2_mapper(
    request: GenerationRequest,
    api: GPTImage2API,
) -> NodeOutput:
    """GPT-Image2 的自适应映射+执行 (支持文生图/图生图/图像编辑)"""
    model = request.params.get("model") or "gpt-image2"
    ratio = request.ratio or _calculate_aspect_ratio(request.width, request.height)

    # 动态参数判定：如果提供了图片则走 Dall-e 的图生图接口，否则走文生图
    if request.images:
        image = await api.draw_image(
            model=model,
            prompt=request.prompt,
            aspect_ratio=ratio,
            image_list=request.images,
        )
    else:
        image = await api.draw_image(
            model=model,
            prompt=request.prompt,
            aspect_ratio=ratio,
        )

    if isinstance(image, int):
        raise RuntimeError(f"GPT-Image2 生成失败，错误码: {image}")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    data = buf.getvalue()

    return NodeOutput(
        status="ok",
        output_type="image",
        data=data,
        mime_type="image/png",
        outputs={"image": data},
    )
