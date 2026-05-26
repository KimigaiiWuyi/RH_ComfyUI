"""视频生成映射函数"""

from __future__ import annotations

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def wan_text2video_mapper(
    request: GenerationRequest,
    workflow: dict,
    api: ComfyUIAPI,
) -> dict:
    """Wan 2.2 文生视频工作流的参数映射"""
    workflow["37"]["inputs"]["text"] = request.prompt
    workflow["44"]["inputs"]["value"] = request.width
    workflow["34"]["inputs"]["value"] = request.height
    workflow["33"]["inputs"]["value"] = request.duration
    return workflow


async def wan_img2video_mapper(
    request: GenerationRequest,
    workflow: dict,
    api: ComfyUIAPI,
) -> dict:
    """Wan 2.2 图生视频工作流的参数映射"""
    workflow["102"]["inputs"]["text"] = request.prompt
    workflow["289"]["inputs"]["value"] = request.width
    workflow["290"]["inputs"]["value"] = request.height
    workflow["294"]["inputs"]["value"] = request.duration
    if request.images:
        workflow["67"]["inputs"]["image"] = await api.upload_image(request.images[0])
    return workflow
