"""GeminiOmniChannel — 把 Gemini Omni Interactions 接成通用 ProviderChannel

挂在 gemini_omni12 上:对外仍是目录名,vendor model id(gemini-omni-1.2-flash)
只在内部请求里出现。凭证/Vertex/中转与生图共用 ``Gemini_Image_*`` 配置,
启用列表同样在「启用的 Gemini 模型」勾选。出错抛可重试 ChannelError;
429/503 标 transient 做原通道排队退避。interactions.cancel 支持远端取消。
"""

from __future__ import annotations

from typing import Any, Optional

from .api import gemini_omni_api
from ...audit_key import mask_api_key
from ...core.types import NodeOutput
from ....core.base.errors import ChannelError
from ..gemini_image.config import gemini_disabled_reason, is_gemini_model_enabled
from ...mappers.gemini_omni import gemini_omni_mapper
from ..gemini_image.channel import (
    GEMINI_VERTEX_PROVIDER,
    GEMINI_AI_STUDIO_PROVIDER,
    _is_rate_limited,
)
from ....core.channels.channel import ProviderChannel
from ....core.channels.failure import Phase, FailureKind


class GeminiOmniChannel(ProviderChannel):
    """Gemini Omni 视频通道(VertexAI / AI Studio 双模;Interactions API)"""

    name = "gemini-omni"
    weight = 2

    def __init__(self, logical_model: str = "") -> None:
        self._api = gemini_omni_api
        self._logical_model = logical_model

    def supports_remote_cancel(self) -> bool:
        """background interaction 有官方 cancel 端点。"""
        return True

    async def check_available(self) -> bool:
        if self._logical_model and not is_gemini_model_enabled(self._logical_model):
            return False
        return self._api.is_configured()

    async def unavailable_reason(self) -> str:
        if self._logical_model:
            disabled = gemini_disabled_reason(self._logical_model, self._logical_model)
            if disabled is not None:
                return disabled
        return "未配置 Gemini(AI Studio 需 Gemini_Image_apikey;Vertex 需 Gemini_Image_Project_ID)"

    def audit_key_prefix(self) -> str:
        return mask_api_key(self._api.api_key or self._api.project_id or "")

    def audit_prefix_names(self) -> dict[str, str]:
        key = mask_api_key(self._api.api_key or "")
        project = mask_api_key(self._api.project_id or "")
        current = key or project
        found: dict[str, str] = {}
        if self.name.strip() and current:
            found[self.name.strip()] = project if self._api.is_vertex and project else current
        if current:
            found[GEMINI_AI_STUDIO_PROVIDER] = key or current
            found[GEMINI_VERTEX_PROVIDER] = project or current
        return found

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request = kwargs["request"]
        vendor_model: Optional[str] = kwargs.get("vendor_model")
        if self._logical_model:
            disabled = gemini_disabled_reason(self._logical_model, self._logical_model)
            if disabled is not None:
                raise ChannelError(
                    disabled,
                    kind=FailureKind.CAPABILITY,
                    phase=Phase.BEFORE_SEND,
                    retryable=True,
                    channel=self.name,
                    user_message=disabled,
                )
        if not self._api.is_configured():
            raise ChannelError(
                "Gemini Omni 未配置(AI Studio 需 API Key;Vertex 需 Project ID)",
                kind=FailureKind.AUTH_OR_QUOTA,
                phase=Phase.BEFORE_SEND,
                retryable=True,
                channel=self.name,
                user_message="该供应商未配置完整凭证。",
            )
        if vendor_model:
            request.params["model"] = vendor_model

        try:
            output = await gemini_omni_mapper(request, self._api)
        except Exception as exc:  # 统一翻译成可切换/可排队的通道错误
            rate_limited = _is_rate_limited(exc)
            # 4xx(非 429)是请求本身的问题:参数/内容类错误换通道也不会成功
            status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            request_fault = (
                not rate_limited
                and status_code is not None
                and str(status_code).isdigit()
                and 400 <= int(status_code) < 500
            )
            raise ChannelError(
                f"Gemini Omni 视频生成失败: {exc}",
                kind=(
                    FailureKind.RATE_LIMITED
                    if rate_limited
                    else (FailureKind.INVALID_REQUEST if request_fault else FailureKind.PROVIDER_FAULT)
                ),
                phase=Phase.REJECTED,
                retryable=not request_fault,
                transient=rate_limited or None,
                channel=self.name,
                code=(
                    "RATE_LIMITED"
                    if rate_limited
                    else (f"HTTP_{status_code}" if request_fault else "GEMINI_OMNI_FAILED")
                ),
                user_message=(
                    "Gemini Omni 视频生成繁忙,正在排队重试…"
                    if rate_limited
                    else "Gemini Omni 视频生成失败,请稍后重试。"
                ),
            ) from exc

        stored = GEMINI_VERTEX_PROVIDER if self._api.is_vertex else GEMINI_AI_STUDIO_PROVIDER
        output.metadata.setdefault("channel", stored)
        return output


__all__ = ["GeminiOmniChannel"]
