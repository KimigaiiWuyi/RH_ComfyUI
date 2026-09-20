"""GPT-Image2 生图模型自适应映射函数"""

from __future__ import annotations

from typing import Protocol

from PIL import Image

from ..core.types import NodeOutput
from ..core.request import GenerationRequest
from ..image_process import encode_pil_image
from .gpt_image2_params import (
    resolve_gpt_image2_background,
    resolve_gpt_image_family_model,
    resolve_gpt_image2_output_format,
)


class GptImageDrawClient(Protocol):
    async def draw_image(
        self,
        *,
        model: str,
        prompt: str,
        aspect_ratio: str | None = ...,
        image_size: str | None = ...,
        quality: str | None = ...,
        background: str | None = ...,
        output_format: str | None = ...,
        image_list: list[bytes] | None = ...,
    ) -> object: ...


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
    api: GptImageDrawClient,
) -> NodeOutput:
    """GPT-Image2 的自适应映射+执行 (支持文生图/图生图/图像编辑)

    ratio + image_size → size;quality / background / output_format 透传给上游。
    """
    model = resolve_gpt_image_family_model(request)
    ratio = request.ratio or _calculate_aspect_ratio(request.width, request.height)
    image_size = request.params.get("image_size") or "2K"
    quality = request.params.get("quality") or "medium"
    background = resolve_gpt_image2_background(request.params)
    output_format = resolve_gpt_image2_output_format(request.params)

    # 动态参数判定：如果提供了图片则走 Dall-e 的图生图接口，否则走文生图
    if request.images:
        drawn = await api.draw_image(
            model=model,
            prompt=request.prompt,
            aspect_ratio=ratio,
            image_size=image_size,
            quality=quality,
            background=background,
            output_format=output_format,
            image_list=request.images,
        )
    else:
        drawn = await api.draw_image(
            model=model,
            prompt=request.prompt,
            aspect_ratio=ratio,
            image_size=image_size,
            quality=quality,
            background=background,
            output_format=output_format,
        )

    if isinstance(drawn, int):
        raise RuntimeError(f"{model} 生成失败，错误码: {drawn}")

    from ..backends.gpt_image2.api import GPTImageDrawResult

    usage: dict[str, object] = {}
    raw: dict[str, object] = {}
    if isinstance(drawn, GPTImageDrawResult):
        image = drawn.image
        usage = drawn.usage
        raw = drawn.raw
    elif isinstance(drawn, Image.Image):
        image = drawn
    else:
        raise RuntimeError(f"{model} 返回了无法处理的类型: {type(drawn)}")

    data, mime = encode_pil_image(image, output_format)

    return NodeOutput(
        status="ok",
        output_type="image",
        data=data,
        mime_type=mime,
        outputs={"image": data},
        usage=dict(usage),
        raw=dict(raw),
    )
