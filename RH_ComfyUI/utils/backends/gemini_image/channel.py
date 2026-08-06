"""GeminiImageChannel — 把 Gemini Interactions API 接成一个通用 ProviderChannel

挂在 banana1 / banana2 / banana_pro 上:对外仍是统一模型名,vendor model id
(如 gemini-3.1-flash-image-preview / gemini-3-pro-image-preview)只在内部请求里出现。
没填 key → 不可用自动让路;出错抛可重试 ChannelError 切下一通道。
429/503 标 transient,由 run() 做最长 1 小时的原通道排队退避。
"""

from __future__ import annotations

from typing import Any, Optional

from .api import gemini_image_api
from ...core.types import NodeOutput
from ....core.base.errors import ChannelError
from ...mappers.gemini_image import gemini_flash_image_mapper
from ....core.channels.channel import ProviderChannel


def _is_rate_limited(exc: BaseException) -> bool:
    """上游限流/配额耗尽:Resource exhausted、HTTP 429/503 等。"""
    text = str(exc).lower()
    if "resource exhausted" in text or "resource_exhausted" in text:
        return True
    if "rate limit" in text or "too many requests" in text:
        return True
    if "429" in text or "503" in text:
        return True
    # google-genai / grpc 常见码
    code = getattr(exc, "code", None)
    if code is not None and str(code) in {"429", "8", "RESOURCE_EXHAUSTED"}:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    return status in (429, 503)


class GeminiImageChannel(ProviderChannel):
    """Gemini 生图通道(VertexAI / AI Studio 双模;vendor_model 区分 Flash / Pro)"""

    name = "gemini"
    weight = 2

    def __init__(self) -> None:
        self._api = gemini_image_api

    async def check_available(self) -> bool:
        return self._api.is_configured()

    async def unavailable_reason(self) -> str:
        return "未配置 Gemini(AI Studio 需 Gemini_Image_apikey;Vertex 需 Gemini_Image_Project_ID)"

    def audit_key_prefix(self) -> str:
        # AI Studio 记 key 前缀;Vertex 无 key,记 project 前缀
        return (self._api.api_key or self._api.project_id or "")[:6]

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request = kwargs["request"]
        vendor_model: Optional[str] = kwargs.get("vendor_model")
        # Vertex 模式合法地没有 api_key(走 ADC / 服务账号),守卫必须与
        # check_available 同源用 is_configured(),否则 Vertex 永远打不通
        if not self._api.is_configured():
            raise ChannelError(
                "Gemini 生图未配置(AI Studio 需 API Key;Vertex 需 Project ID)",
                retryable=True,
                channel=self.name,
                user_message="该供应商未配置完整凭证。",
            )
        if vendor_model:
            request.params["model"] = vendor_model

        try:
            output = await gemini_flash_image_mapper(request, self._api)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成可切换/可排队的通道错误
            rate_limited = _is_rate_limited(exc)
            raise ChannelError(
                f"Gemini 生图失败: {exc}",
                retryable=True,
                transient=rate_limited,
                channel=self.name,
                code="RATE_LIMITED" if rate_limited else "GEMINI_FAILED",
                user_message=(
                    "Gemini 生图繁忙,正在排队重试…" if rate_limited else "Gemini 生图失败,请稍后重试。"
                ),
            ) from exc

        # 消费统计维度:区分 VertexAI / AI Studio
        output.metadata.setdefault(
            "channel", "gemini-vertex" if self._api.is_vertex else "gemini-ai-studio"
        )
        return output


__all__ = ["GeminiImageChannel"]
