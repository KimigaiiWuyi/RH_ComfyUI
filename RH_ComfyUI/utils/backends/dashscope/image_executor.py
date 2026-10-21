"""DashScope 图像 Adapter。名字是 dashscope_image,同步出图。

不能叫 dashscope:那个名字在目录里表示可远程取消的视频通道。
"""

from __future__ import annotations

from ..base import Adapter
from .config import dashscope_credentials
from .image_api import DashScopeImageAPI
from ...audit_key import mask_api_key
from ...core.types import NodeOutput, ProgressEvent, ProgressCallback, CapabilityManifest
from ...core.request import GenerationRequest
from ...core.pipeline import NodeDef


class DashScopeImageAdapter(Adapter):
    name = "dashscope_image"

    def __init__(self) -> None:
        self.api = DashScopeImageAPI()

    async def check_available(self) -> bool:
        return self.api.is_configured()

    async def get_unavailable_reason(self) -> str:
        creds = dashscope_credentials()
        if not creds.api_key.strip():
            return "未配置 DashScope API Key,请在 Web 控制台填写 HappyHorse_apikey_dashscope"
        if not creds.enabled:
            return "DashScope 未启用:请在 Web 控制台打开 HappyHorse_Enable_dashscope"
        return "DashScope 图像暂不可用"

    def audit_key_prefix(self) -> str:
        return mask_api_key(dashscope_credentials().api_key)

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image"],
            supported_params=["prompt", "images", "ratio", "image_size", "n"],
            output_mime=["image/png"],
            mode="sync",
            priority=66,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress: ProgressCallback | None = None,
    ) -> NodeOutput:
        if node.mapper_func is None:
            raise RuntimeError(f"DashScope 图像节点 {node.name} 缺少 mapper_func")
        request.params["model"] = node.backend_model or node.name
        if on_progress is not None:
            await on_progress(ProgressEvent(stage="running", percent=20, message="DashScope 出图中"))
        result = await node.mapper_func(request, self.api)
        if on_progress is not None:
            await on_progress(ProgressEvent(stage="done", percent=100, message="完成"))
        if isinstance(result, NodeOutput):
            return result
        raise RuntimeError(f"DashScope 图像节点 {node.name} 返回了无法处理的类型: {type(result)}")


__all__ = ["DashScopeImageAdapter"]
