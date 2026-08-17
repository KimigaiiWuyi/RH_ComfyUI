"""腾讯云混元扩图 Adapter — ImageOutpainting"""

from __future__ import annotations

from .api import tx_aiart_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationRequest
from ...core.pipeline import NodeDef


class TxAiartAdapter(Adapter):
    """腾讯云混元 AI 艺术后端(目前仅扩图)。"""

    name = "tx_aiart"

    def __init__(self) -> None:
        self.api = tx_aiart_api

    async def check_available(self) -> bool:
        return self.api.configured()

    async def get_unavailable_reason(self) -> str:
        return "未配置腾讯云混元扩图凭证,请在 Web 控制台填写 TX_AIArt_secret_id / TX_AIArt_secret_key"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image"],
            supported_params=["images", "ratio", "prompt"],
            output_mime=["image/png"],
            mode="sync",
            priority=60,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        if not request.images:
            raise RuntimeError(f"{node.display_name}:必须提供 1 张待扩图原图")
        params = request.params or {}
        ratio = str(params.get("ratio") or request.ratio or "").strip()
        if not ratio:
            raise RuntimeError(f"{node.display_name}:缺少 ratio")

        from ....core.telemetry.wire_capture import set_wire_audit

        set_wire_audit(
            prompt=request.prompt or "",
            request={
                "model": node.name,
                "action": "ImageOutpainting",
                "ratio": ratio,
                "region": self.api.region,
            },
        )
        await _emit(on_progress, ProgressEvent(stage="running", percent=15, message="腾讯云扩图中"))
        png = await self.api.image_outpainting(request.images[0], ratio)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))
        return NodeOutput(
            status="ok",
            output_type="image",
            data=png,
            mime_type="image/png",
        )


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass
