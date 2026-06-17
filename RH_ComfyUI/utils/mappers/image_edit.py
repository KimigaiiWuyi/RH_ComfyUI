"""图片编辑映射函数"""

from __future__ import annotations

from typing import Any

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def qwen_edit_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """千问编辑工作流的参数映射"""
    workflow["103"]["inputs"]["text"] = request.prompt

    img_slot = ["41", "79", "81"]
    img_slot2 = ["73", "79", "81"]
    for index, img_bytes in enumerate(request.images):
        if index >= 3:
            break

        workflow[img_slot[index]]["inputs"]["image"] = await api.upload_image(img_bytes)

        for j in ["68", "69"]:
            workflow[j]["inputs"][f"image{index + 1}"] = [img_slot2[index], 0]

    return workflow
