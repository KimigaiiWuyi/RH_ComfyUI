"""Seedance Adapter — 火山方舟 Seedance 2.0 视频生成后端

特点:
- 任务异步轮询(async_poll 模式),通过 on_progress 上报进度
- 支持 6 类任务:text2video / image2video / first_last_frame2video /
  multimodal2video / video_edit / draft(样片)
- model ID 由 NodeDef.backend_model 注入,通过 request.params["model"] 传给 mapper
"""

from __future__ import annotations

from typing import Optional

from .api import seedance_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationRequest
from ...core.pipeline import NodeDef


class SeedanceAdapter(Adapter):
    """Seedance 2.0 后端"""

    name = "seedance"

    def __init__(self) -> None:
        self.api = seedance_api
        self._cached_key: Optional[str] = None

    def _ensure_credentials(self) -> None:
        """按需从配置读取 API Key"""
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        key = SERVICE_CONFIG.get_config("Seedance_apikey").data or ""
        if key != self._cached_key:
            base_url = SERVICE_CONFIG.get_config("Seedance_BaseURL").data or None
            self.api.update_credentials(key, base_url)
            self._cached_key = key

    # ── Adapter 接口 ──

    async def check_available(self) -> bool:
        self._ensure_credentials()
        return bool(self.api.api_key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 Seedance API Key,请在 Web 控制台配置 Seedance_apikey"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["video"],
            supported_params=[
                "prompt",
                "images",
                "video_refs",
                "audio_refs",
                "ordered_content",
                "ratio",
                "resolution",
                "duration",
                "seed",
                "generate_audio",
                "watermark",
                "camera_fixed",
                "return_last_frame",
                "service_tier",
            ],
            output_mime=["video/mp4", "image/png"],
            mode="async_poll",
            priority=80,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        self._ensure_credentials()

        if not self.api.api_key:
            raise RuntimeError("Seedance API Key 未配置")

        if node.mapper_func is None:
            raise RuntimeError(f"Seedance 节点 {node.name} 缺少 mapper_func")

        # 注入 backend_model(由 YAML 声明的 vendor model id)
        if node.backend_model:
            request.params["model"] = node.backend_model

        # 透传进度
        return await node.mapper_func(request, self.api, on_progress=on_progress)


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass


# 向后兼容
SeedanceBackend = SeedanceAdapter
