"""XiaoMi MiMo TTS Adapter"""

from __future__ import annotations

from .api import mimo_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationResult, GenerationRequest
from ...core.pipeline import NodeDef


class MIMOAdapter(Adapter):
    """XiaoMi MiMo-V2.5-TTS 系列语音合成"""

    name = "mimo"

    def __init__(self) -> None:
        self.api = mimo_api

    # ── Adapter 接口 ──

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        key: str = SERVICE_CONFIG.get_config("MIMO_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 MiMo API Key,请在 Web 控制台配置 MIMO_apikey"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["speech"],
            supported_params=["prompt", "mood", "voice_id", "speed", "reference_audio"],
            output_mime=["audio/wav", "audio/mpeg"],
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
        if node.mapper_func is None:
            raise RuntimeError(f"MiMo 节点 {node.name} 缺少 mapper_func")

        await _emit(on_progress, ProgressEvent(stage="running", percent=20, message="MiMo TTS 生成中"))
        result = await node.mapper_func(request, self.api)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))

        if isinstance(result, NodeOutput):
            return result
        if isinstance(result, GenerationResult):
            return NodeOutput.from_result(result)

        if isinstance(result, bytes):
            return NodeOutput(
                status="ok",
                output_type="audio",
                data=result,
                mime_type="audio/wav",
            )

        raise RuntimeError(f"MiMo 节点 {node.name} 返回了无法处理的类型: {type(result)}")


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass


# 向后兼容
MIMOBackend = MIMOAdapter
