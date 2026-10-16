"""Gemini 语音 Adapter。同步出音频,无远程取消。"""

from __future__ import annotations

from .api import GeminiSpeechAPI
from ..base import Adapter
from ...audit_key import mask_api_key
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationResult, GenerationRequest
from ...core.pipeline import NodeDef
from ..gemini_image.api import GeminiImageAPI


class GeminiSpeechAdapter(Adapter):
    name = "gemini_speech"

    def __init__(self) -> None:
        self.api = GeminiSpeechAPI()

    async def check_available(self) -> bool:
        return self.api.is_configured()

    async def get_unavailable_reason(self) -> str:
        return "未配置 Gemini(AI Studio 需 Gemini_Image_apikey;Vertex 需 Gemini_Image_Project_ID)"

    def audit_key_prefix(self) -> str:
        creds = GeminiImageAPI()
        return mask_api_key(creds.api_key or creds.project_id or "")

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["speech"],
            supported_params=["prompt", "mood", "voice_id"],
            output_mime=["audio/wav"],
            mode="sync",
            priority=66,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        if node.mapper_func is None:
            raise RuntimeError(f"Gemini 语音节点 {node.name} 缺少 mapper_func")
        if on_progress is not None:
            await on_progress(ProgressEvent(stage="running", percent=20, message="Gemini 合成中"))
        result = await node.mapper_func(request, self.api)
        if on_progress is not None:
            await on_progress(ProgressEvent(stage="done", percent=100, message="完成"))
        if isinstance(result, NodeOutput):
            return result
        if not isinstance(result, GenerationResult):
            raise RuntimeError(f"Gemini 语音节点 {node.name} 返回了无法处理的类型: {type(result)}")
        # from_result 只留 points,用量要补回去,后结算才读得到 usage_metadata。
        out = NodeOutput.from_result(result)
        for key, value in result.usage.items():
            out.usage[key] = value
        for key, value in result.raw.items():
            out.raw[key] = value
        return out


__all__ = ["GeminiSpeechAdapter"]
