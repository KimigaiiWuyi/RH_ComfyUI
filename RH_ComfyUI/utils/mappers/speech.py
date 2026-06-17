"""语音生成映射函数"""

from __future__ import annotations

from typing import Any

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def index_tts2_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """IndexTTS2 语音生成工作流的参数映射"""
    workflow["14"]["inputs"]["value"] = request.prompt

    # 上传参考音频到 ComfyUI，获取服务器端文件名并填入 LoadAudio 节点
    if request.reference_audio is not None:
        audio_name = await api.upload_mp3(request.reference_audio)
        workflow["13"]["inputs"]["audio"] = audio_name

    # 设置情绪描述文本（填入 slot 40 节点）
    if request.mood is not None:
        workflow["40"]["inputs"]["value"] = request.mood

    return workflow
