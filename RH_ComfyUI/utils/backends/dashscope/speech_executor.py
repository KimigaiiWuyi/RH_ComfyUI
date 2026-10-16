"""DashScope 语音 Adapter。名字是 dashscope_tts,不复用视频通道。"""

from __future__ import annotations

from ..base import Adapter
from .config import dashscope_credentials
from .speech_api import DashScopeSpeechAPI
from ...audit_key import mask_api_key
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationResult, GenerationRequest
from ...core.pipeline import NodeDef


class DashScopeTtsAdapter(Adapter):
    name = "dashscope_tts"

    def __init__(self) -> None:
        self.api = DashScopeSpeechAPI()

    async def check_available(self) -> bool:
        return self.api.is_configured()

    async def get_unavailable_reason(self) -> str:
        creds = dashscope_credentials()
        if not creds.api_key:
            return "未配置 DashScope API Key,请在 Web 控制台填写 HappyHorse_apikey_dashscope"
        if not creds.enabled:
            return "DashScope 未启用:请在 Web 控制台打开 HappyHorse_Enable_dashscope"
        return "DashScope 语音暂不可用"

    def audit_key_prefix(self) -> str:
        return mask_api_key(dashscope_credentials().api_key)

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["speech"],
            supported_params=["prompt", "mood", "voice_id", "speed"],
            output_mime=["audio/mpeg"],
            mode="sync",
            priority=64,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        if node.mapper_func is None:
            raise RuntimeError(f"DashScope 语音节点 {node.name} 缺少 mapper_func")
        if on_progress is not None:
            await on_progress(ProgressEvent(stage="running", percent=20, message="DashScope 合成中"))
        result = await node.mapper_func(request, self.api)
        if on_progress is not None:
            await on_progress(ProgressEvent(stage="done", percent=100, message="完成"))
        if isinstance(result, NodeOutput):
            return result
        if not isinstance(result, GenerationResult):
            raise RuntimeError(f"DashScope 语音节点 {node.name} 返回了无法处理的类型: {type(result)}")
        # from_result 丢掉 usage。字符数 / token 要留在 NodeOutput 上,后结算才看得到。
        out = NodeOutput.from_result(result)
        for key, value in result.usage.items():
            out.usage[key] = value
        for key, value in result.raw.items():
            out.raw[key] = value
        return out


__all__ = ["DashScopeTtsAdapter"]
