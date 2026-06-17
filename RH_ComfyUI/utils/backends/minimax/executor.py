"""MiniMax Adapter — 火山方舟图像生成与语音合成"""

from __future__ import annotations

import io

from PIL import Image

from .api import minimax_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import (
    TASK_MIME_MAP,
    TASK_OUTPUT_MAP,
    OutputType,
    GenerationResult,
    GenerationRequest,
)
from ...core.pipeline import NodeDef


class MiniMaxAdapter(Adapter):
    """MiniMax 后端 — 图像生成 + T2A 语音合成"""

    name = "minimax"

    def __init__(self) -> None:
        self.api = minimax_api

    # ── Adapter 接口 ──

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        key: str = SERVICE_CONFIG.get_config("MiniMax_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 MiniMax API Key,请在 Web 控制台配置 MiniMax_apikey"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image", "speech"],
            supported_params=[
                "prompt",
                "images",
                "ratio",
                "voice_id",
                "speed",
                "mood",
                "language_boost",
            ],
            output_mime=["image/png", "audio/mpeg", "audio/wav"],
            mode="async_poll",
            priority=70,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        if node.mapper_func is None:
            raise RuntimeError(f"MiniMax 节点 {node.name} 缺少 mapper_func")

        await _emit(on_progress, ProgressEvent(stage="running", percent=15, message="MiniMax 生成中"))
        result = await node.mapper_func(request, self.api)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))

        # mapper 可能返回多种类型
        if isinstance(result, NodeOutput):
            return result
        if isinstance(result, GenerationResult):
            return NodeOutput.from_result(result)

        if isinstance(result, list) and result and isinstance(result[0], Image.Image):
            img = result[0]
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            return NodeOutput(
                status="ok",
                output_type="image",
                data=data,
                mime_type="image/png",
            )

        if isinstance(result, Image.Image):
            buf = io.BytesIO()
            result.save(buf, format="PNG")
            data = buf.getvalue()
            return NodeOutput(
                status="ok",
                output_type="image",
                data=data,
                mime_type="image/png",
            )

        if isinstance(result, bytes):
            output_type = TASK_OUTPUT_MAP.get(node.task_type, OutputType.IMAGE)
            mime_type = TASK_MIME_MAP.get(node.task_type, "image/png")
            return NodeOutput(
                status="ok",
                output_type=output_type.value,
                data=result,
                mime_type=mime_type,
            )

        raise RuntimeError(f"MiniMax 节点 {node.name} 返回了无法处理的类型: {type(result)}")


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass


# 向后兼容
MiniMaxBackend = MiniMaxAdapter
