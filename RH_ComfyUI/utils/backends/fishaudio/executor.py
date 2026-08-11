"""Fish Audio Adapter — S2 系列 TTS 语音合成 + ASR 语音识别"""

from __future__ import annotations

from .api import fishaudio_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationResult, GenerationRequest
from ...core.pipeline import NodeDef


class FishAudioAdapter(Adapter):
    """Fish Audio 官方 TTS(自动音色克隆 + 内联情绪)与 ASR(语音识别)"""

    name = "fishaudio"

    def __init__(self) -> None:
        self.api = fishaudio_api

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        key: str = SERVICE_CONFIG.get_config("FishAudio_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 Fish Audio API Key,请在 Web 控制台配置 FishAudio_apikey"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["speech", "asr"],
            supported_params=[
                # TTS
                "prompt",
                "mood",
                "reference_audio",
                "speed",
                # ASR
                "audio",
                "audio_refs",
                "language_boost",
            ],
            output_mime=["audio/mpeg", "text/plain; charset=utf-8"],
            mode="sync",
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
            raise RuntimeError(f"Fish Audio 节点 {node.name} 缺少 mapper_func")

        from ....core.telemetry.wire_capture import set_wire_audit

        set_wire_audit(
            prompt=request.prompt or "",
            request={
                "backend": "fishaudio",
                "task": str(getattr(getattr(node, "task_type", None), "value", "") or ""),
                "prompt": request.prompt or "",
                "mood": request.mood,
                "voice_id": request.voice_id,
            },
        )
        # 区分提示语:按 node.task_type 推断(TTS / ASR)
        if getattr(node, "task_type", None) is not None and str(node.task_type.value) == "asr":
            await _emit(on_progress, ProgressEvent(stage="running", percent=20, message="Fish Audio 识别中"))
        else:
            await _emit(on_progress, ProgressEvent(stage="running", percent=20, message="Fish Audio 合成中"))

        result = await node.mapper_func(request, self.api)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))

        if isinstance(result, NodeOutput):
            return result
        if isinstance(result, GenerationResult):
            return NodeOutput.from_result(result)
        if isinstance(result, bytes):
            # 默认按 bytes 处理:TTS 是 mp3,ASR 是 UTF-8 文本 —— mime 由 mapper 给
            task_type = getattr(node, "task_type", None)
            output_type = "audio" if task_type is None or str(task_type.value) == "speech" else "text"
            mime_type = "audio/mpeg" if output_type == "audio" else "text/plain; charset=utf-8"
            return NodeOutput(
                status="ok",
                output_type=output_type,
                data=result,
                mime_type=mime_type,
            )

        raise RuntimeError(f"Fish Audio 节点 {node.name} 返回了无法处理的类型: {type(result)}")


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass
