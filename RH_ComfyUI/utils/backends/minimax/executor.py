"""MiniMax 图像生成后端执行器 — 实现 Backend 接口"""

from __future__ import annotations

import io

from PIL import Image

from .api import minimax_api
from ..base import Backend
from ...core.request import OutputType, GenerationResult, GenerationRequest
from ...core.pipeline import PipelineDef


class MiniMaxBackend(Backend):
    """MiniMax 图像生成后端

    通过 MiniMax /v1/image_generation API 进行文生图和图生图，
    支持 image-01 和 image-01-live 模型。
    """

    name = "minimax"

    def __init__(self) -> None:
        self.api = minimax_api

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import RHCOMFYUI_CONFIG

        key: str = RHCOMFYUI_CONFIG.get_config("MiniMax_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 MiniMax API Key，请在 Web 控制台配置 MiniMax_apikey"

    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        """MiniMax 后端不走工作流，直接调 mapper_func 执行"""
        if pipeline.mapper_func is None:
            raise RuntimeError(f"MiniMax Pipeline {pipeline.name} 缺少 mapper_func")

        result = await pipeline.mapper_func(request, self.api)

        # mapper_func 可能返回 GenerationResult 或 PIL.Image 列表
        if isinstance(result, GenerationResult):
            return result

        if isinstance(result, list) and result and isinstance(result[0], Image.Image):
            # 多张图片时拼接为单张（取第一张）
            img = result[0]
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            return GenerationResult(
                output_type=OutputType.IMAGE,
                data=image_bytes,
                mime_type="image/png",
            )

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

        raise RuntimeError(f"MiniMax Pipeline {pipeline.name} 返回了无法处理的类型: {type(result)}")
