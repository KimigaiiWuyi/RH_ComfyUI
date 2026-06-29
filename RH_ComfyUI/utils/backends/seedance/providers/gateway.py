"""聚合网关 Seedance Provider

实现要点:
- 端点: POST /video/generation/tasks
- 请求体: 与 ARK 同构(content[] + 通用参数)
- 自动注入 Idempotency-Key 头(UUID v4),防重试重复计费
- 响应: {code,msg,data} 信封,创建返回 data.taskId
- 查询: GET /video/generation/tasks/{id},返回 data.{taskId,status,resultUrl,thumbnailUrl,progress,tokenUsage,failReason}
- 错误: VID-* 业务错误码透传到 SeedanceProviderError.code
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from .ark import _ContentArrayMixin
from ..spec import VideoGenSpec, VideoTaskShape
from ..provider import (
    NormalizedTask,
    NormalizedStatus,
    SeedanceProvider,
    SeedanceProviderError,
    normalize_usage,
)


class GatewaySeedanceProvider(_ContentArrayMixin, SeedanceProvider):
    """聚合网关 Seedance 后端"""

    name = "gateway"
    DEFAULT_BASE_URL = ""  # 由配置注入,无默认

    supported_shapes = {
        VideoTaskShape.TEXT2VIDEO,
        VideoTaskShape.IMAGE2VIDEO,
        VideoTaskShape.FIRST_LAST_FRAME,
        VideoTaskShape.MULTIMODAL,
        VideoTaskShape.VIDEO_EDIT,
        VideoTaskShape.VIDEO_EXTEND,
    }
    supported_resolutions = {"480p", "720p", "1080p", "4k"}

    STATUS_MAP: dict[str, NormalizedStatus] = {
        "queued": NormalizedStatus.QUEUED,
        "pending": NormalizedStatus.QUEUED,
        "running": NormalizedStatus.RUNNING,
        "success": NormalizedStatus.SUCCEEDED,
        "succeeded": NormalizedStatus.SUCCEEDED,
        "failed": NormalizedStatus.FAILED,
        "cancelled": NormalizedStatus.CANCELLED,
        "expired": NormalizedStatus.EXPIRED,
    }

    def _auth_headers(self) -> dict[str, str]:
        return {
            **super()._auth_headers(),
            "Idempotency-Key": str(uuid.uuid4()),
        }

    def _unwrap(self, resp_json: dict[str, Any]) -> dict[str, Any]:
        """解信封 `{code,msg,data}`;失败抛 SeedanceProviderError(带 VID-* code)。"""
        code = resp_json.get("code")
        if code is None:
            return resp_json
        # 容错:网关 code 偶尔返回字符串/非数值,不要让 int() 抛 ValueError
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = 0
        if 200 <= code_int < 300:
            data = resp_json.get("data")
            return data if isinstance(data, dict) else {}
        data = resp_json.get("data") or {}
        msg = data.get("message") or resp_json.get("msg") or "网关错误"
        raise SeedanceProviderError(
            f"网关错误 {code}: {msg}",
            code=str(data.get("code") or "GATEWAY_ERROR"),
            retryable=bool(data.get("retryable", False)),
            http_status=code_int or 500,
            provider=self.name,
            # 对前端仅暴露供应商 message,排查信息(code / 信封)写在 str(exc)。
            user_message=str(msg),
        )

    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        content = await self._build_content(spec)
        body: dict[str, Any] = {"content": content}
        if model:
            body["model"] = model
        if spec.ratio is not None:
            body["ratio"] = spec.ratio
        if spec.duration:
            body["duration"] = int(spec.duration)
        if spec.resolution is not None:
            body["resolution"] = spec.resolution
        if spec.seed is not None:
            body["seed"] = spec.seed
        if spec.generate_audio:
            body["generate_audio"] = True
        if not spec.watermark:
            body["watermark"] = False
        if spec.camera_fixed:
            body["camera_fixed"] = True
        if spec.return_last_frame:
            body["return_last_frame"] = True
        if spec.service_tier and spec.service_tier != "default":
            body["service_tier"] = spec.service_tier
        draft = spec.params.get("draft")
        if draft is not None:
            body["draft"] = draft
        cb = spec.params.get("callback_url")
        if cb:
            body["callback_url"] = cb
        return "POST", f"{self.base_url}/video/generation/tasks", self._auth_headers(), body

    def parse_create(self, resp_json: dict[str, Any]) -> str:
        data = self._unwrap(resp_json)
        return str(data.get("taskId") or "")

    async def get(self, task_id: str) -> NormalizedTask:
        url = f"{self.base_url}/video/generation/tasks/{task_id}"
        j = await self._request("GET", url, headers=self._auth_headers())
        d = self._unwrap(j)
        return NormalizedTask(
            id=str(d.get("taskId") or task_id),
            status=self.map_status(str(d.get("status") or "")),
            video_url=d.get("resultUrl"),
            last_frame_url=d.get("thumbnailUrl"),
            progress=d.get("progress"),
            usage=normalize_usage("gateway", d.get("tokenUsage") or {}),
            error=d.get("failReason"),
            raw=j,
        )

    async def delete(self, task_id: str) -> None:
        url = f"{self.base_url}/video/generation/tasks/{task_id}"
        await self._request("DELETE", url, headers=self._auth_headers())


__all__ = ["GatewaySeedanceProvider"]
