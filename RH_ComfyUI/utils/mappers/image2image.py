"""图生图映射函数"""

from __future__ import annotations

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def qwen_img2img_mapper(
    request: GenerationRequest,
    workflow: dict,
    api: ComfyUIAPI,
) -> dict:
    """千问图生图工作流的参数映射"""
    workflow["23"]["inputs"]["text"] = request.prompt
    if request.images:
        workflow["41"]["inputs"]["image"] = await api.upload_image(request.images[0])
    return workflow
