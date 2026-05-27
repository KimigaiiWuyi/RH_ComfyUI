"""MiniMax 后端执行器 — 实现 Backend 接口，支持图像生成与语音合成"""

from __future__ import annotations

import io

from PIL import Image

from .api import minimax_api
from ..base import Backend
from ...core.request import OutputType, GenerationResult, GenerationRequest
from ...core.pipeline import PipelineDef


class MiniMaxBackend(Backend):
    """MiniMax 后端

    通过 MiniMax API 进行：
    - 图像生成（/v1/image_generation）：文生图、图生图
    - 语音合成（/v1/t2a_async_v2）：异步语音合成
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
            # 根据 pipeline 的 task_type 判断输出类型
            from ...core.request import TASK_MIME_MAP, TASK_OUTPUT_MAP

            output_type = TASK_OUTPUT_MAP.get(pipeline.task_type, OutputType.IMAGE)
            mime_type = TASK_MIME_MAP.get(pipeline.task_type, "image/png")
            return GenerationResult(
                output_type=output_type,
                data=result,
                mime_type=mime_type,
            )

        raise RuntimeError(f"MiniMax Pipeline {pipeline.name} 返回了无法处理的类型: {type(result)}")
