"""GeminiImageChannel — 把 Gemini Interactions API 接成一个通用 ProviderChannel

作为 banana2(Nano Banana 2)的一路供应商:对外仍是 Nano Banana 2,内部与
OpenAI 兼容网关通道一起参与负载均衡。没填 key → 不可用自动让路;出错抛可重试
ChannelError 切下一通道。vendor model id(gemini-3.1-flash-image-preview)只在
内部请求里出现。
"""

from __future__ import annotations

from typing import Any, Optional

from .api import gemini_image_api
from ...core.types import NodeOutput
from ....core.base.errors import ChannelError
from ...mappers.gemini_image import gemini_flash_image_mapper
from ....core.channels.channel import ProviderChannel


class GeminiImageChannel(ProviderChannel):
    """Gemini 3.1 Flash Image 通道(VertexAI / AI Studio 双模)"""

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
        if not self._api.api_key:
            raise ChannelError(
                "Gemini 生图未配置 API Key",
                retryable=True,
                channel=self.name,
                user_message="该供应商未配置 API Key。",
            )
        if vendor_model:
            request.params["model"] = vendor_model

        try:
            output = await gemini_flash_image_mapper(request, self._api)
        except Exception as exc:  # noqa: BLE001 - 统一翻译成可切换的通道错误
            raise ChannelError(
                f"Gemini 生图失败: {exc}",
                retryable=True,
                channel=self.name,
                user_message="Gemini 生图失败,请稍后重试。",
            ) from exc

        # 消费统计维度:区分 VertexAI / AI Studio
        output.metadata.setdefault(
            "channel", "gemini-vertex" if self._api.is_vertex else "gemini-ai-studio"
        )
        return output


__all__ = ["GeminiImageChannel"]
