"""BLT 后端执行器 — 实现 Backend 接口"""

from __future__ import annotations

import io

from PIL import Image

from .api import blt_api
from ..base import Backend
from ...core.request import OutputType, GenerationResult, GenerationRequest
from ...core.pipeline import PipelineDef


class BLTBackend(Backend):
    """BLT / OpenAI 兼容 API 后端"""

    name = "blt"

    def __init__(self) -> None:
        self.api = blt_api

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import RHCOMFYUI_CONFIG

        key: str = RHCOMFYUI_CONFIG.get_config("BLT_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 BLT API Key，请在 Web 控制台配置 BLT_apikey"

    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        """BLT 后端不走工作流，直接调 mapper_func 执行"""
        if pipeline.mapper_func is None:
            raise RuntimeError(f"BLT Pipeline {pipeline.name} 缺少 mapper_func")

        result = await pipeline.mapper_func(request, self.api)

        # mapper_func 可能返回 GenerationResult 或 PIL.Image
        if isinstance(result, GenerationResult):
            return result

        if isinstance(result, Image.Image):
            buf = io.BytesIO()
            result.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            return GenerationResult(
                output_type=OutputType.IMAGE,
                data=image_bytes,
                mime_type="image/png",
            )

        if isinstance(result, bytes):
            return GenerationResult(
                output_type=OutputType.IMAGE,
                data=result,
                mime_type="image/png",
            )

        raise RuntimeError(f"BLT Pipeline {pipeline.name} 返回了无法处理的类型: {type(result)}")
