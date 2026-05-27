"""XiaoMi MiMo TTS 后端执行器 — 实现 Backend 接口"""

from __future__ import annotations

from .api import mimo_api
from ..base import Backend
from ...core.request import OutputType, GenerationResult, GenerationRequest
from ...core.pipeline import PipelineDef


class MIMOBackend(Backend):
    """XiaoMi MiMo TTS 后端

    通过 MiMo-V2.5-TTS 系列 API 进行语音合成：
    - mimo-v2.5-tts：预置音色语音合成
    - mimo-v2.5-tts-voicedesign：文本描述音色设计
    - mimo-v2.5-tts-voiceclone：音频样本音色复刻
    """

    name = "mimo"

    def __init__(self) -> None:
        self.api = mimo_api

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import RHCOMFYUI_CONFIG

        key: str = RHCOMFYUI_CONFIG.get_config("MIMO_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 MiMo API Key，请在 Web 控制台配置 MIMO_apikey"

    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        """MiMo 后端不走工作流，直接调 mapper_func 执行"""
        if pipeline.mapper_func is None:
            raise RuntimeError(f"MiMo Pipeline {pipeline.name} 缺少 mapper_func")

        result = await pipeline.mapper_func(request, self.api)

        if isinstance(result, GenerationResult):
            return result

        if isinstance(result, bytes):
            return GenerationResult(
                output_type=OutputType.AUDIO,
                data=result,
                mime_type="audio/wav",
            )

        raise RuntimeError(f"MiMo Pipeline {pipeline.name} 返回了无法处理的类型: {type(result)}")
