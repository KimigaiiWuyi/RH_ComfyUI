"""音乐生成映射函数"""

from __future__ import annotations

from typing import Any

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def ace_step_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """ACE Step 1.5 音乐生成工作流的参数映射"""
    # prompt 作为风格描述
    workflow["131"]["inputs"]["text"] = request.prompt
    # negative_prompt 作为歌词（可选）
    workflow["130"]["inputs"]["text"] = request.negative_prompt if request.negative_prompt else ""
    return workflow
