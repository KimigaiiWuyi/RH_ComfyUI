"""GPT-Image2 生图模型自适应映射函数"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from ..core.types import NodeOutput
from ..core.request import GenerationRequest

if TYPE_CHECKING:
    from ..backends.gpt_image2.api import GPTImage2API


def _calculate_aspect_ratio(w: int, h: int) -> str:
    """根据宽度和高度自动计算最接近的宽高比(候选来自像素真源表)。"""
    from .gpt_image2_billing import _RATIO_SIZE_MAP

    actual_ratio = (w / h) if h else 1.0

    def _rv(ratio: str) -> float:
        a, _, b = ratio.partition(":")
        try:
            return int(a) / int(b) if b and int(b) else 1.0
        except ValueError:
            return 1.0

    return min(_RATIO_SIZE_MAP.keys(), key=lambda k: abs(_rv(k) - actual_ratio))


async def gpt_image2_mapper(
    request: GenerationRequest,
    api: GPTImage2API,
) -> NodeOutput:
    """GPT-Image2 的自适应映射+执行 (支持文生图/图生图/图像编辑)

    ratio + image_size → size;quality 透传给上游。
    """
    model = request.params.get("model") or "gpt-image-2"
    ratio = request.ratio or _calculate_aspect_ratio(request.width, request.height)
    image_size = request.params.get("image_size") or "2K"
    quality = request.params.get("quality") or "medium"

    # 动态参数判定：如果提供了图片则走 Dall-e 的图生图接口，否则走文生图
    if request.images:
        image = await api.draw_image(
            model=model,
            prompt=request.prompt,
            aspect_ratio=ratio,
            image_size=image_size,
            quality=quality,
            image_list=request.images,
        )
    else:
        image = await api.draw_image(
            model=model,
            prompt=request.prompt,
            aspect_ratio=ratio,
            image_size=image_size,
            quality=quality,
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
