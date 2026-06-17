"""GPT-Image2 Adapter — OpenAI 兼容协议生图后端执行器

设计要点:
1. 独立的 gpt_image2 后端 — 不复用主 OpenAI 框架,即便用户没装 openai 包也能跑
2. 兼容任何暴露 /v1/images/generations 的 OpenAI 兼容服务
   (OpenAI 官方 / OneAPI / NewAPI / OpenRouter / SiliconFlow / 本地 Ollama 等)
3. 单一物理模型同时支持 text2image / image2image / image_edit,
   通过 request.images 自动分发到对应 API 调用
"""

from __future__ import annotations

import io

from PIL import Image

from .api import gpt_image2_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationResult, GenerationRequest
from ...core.pipeline import NodeDef


class GPTImage2Adapter(Adapter):
    """GPT-Image2 后端适配器"""

    name = "gpt_image2"

    def __init__(self) -> None:
        self.api = gpt_image2_api

    async def check_available(self) -> bool:
        self.api.refresh_config()
        return bool(self.api.api_key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 GPT-Image2 API Key，请在 Web 控制台配置 GPT_Image2_apikey"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image"],
            supported_params=["prompt", "images", "ratio", "width", "height"],
            output_mime=["image/png"],
            mode="sync",
            priority=65,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        self.api.refresh_config()
        if not self.api.api_key:
            raise RuntimeError("GPT-Image2 API Key 未配置")

        if node.mapper_func is None:
            raise RuntimeError(f"GPT-Image2 节点 {node.name} 缺少 mapper_func")

        # 注入 model 名(节点未声明 backend_model 时由 mapper 兜底)
        if node.backend_model:
            request.params["model"] = node.backend_model

        await _emit(on_progress, ProgressEvent(stage="running", percent=10, message="GPT-Image2 生成中"))
        result = await node.mapper_func(request, self.api)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))

        # mapper 可能返回 NodeOutput / GenerationResult / PIL.Image / bytes
        if isinstance(result, NodeOutput):
            return result
        if isinstance(result, GenerationResult):
            return NodeOutput.from_result(result)

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
            return NodeOutput(
                status="ok",
                output_type="image",
                data=result,
                mime_type="image/png",
            )

        raise RuntimeError(f"GPT-Image2 节点 {node.name} 返回了无法处理的类型: {type(result)}")


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass


# 向后兼容
GPTImage2Backend = GPTImage2Adapter