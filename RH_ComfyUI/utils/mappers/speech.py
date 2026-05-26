"""语音生成映射函数"""

from __future__ import annotations

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def index_tts2_mapper(
    request: GenerationRequest,
    workflow: dict,
    api: ComfyUIAPI,
) -> dict:
    """IndexTTS2 语音生成工作流的参数映射"""
    workflow["14"]["inputs"]["value"] = request.prompt
    return workflow
