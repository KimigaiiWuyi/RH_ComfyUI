"""OpenAIImageChannel — 把一家 OpenAI 兼容生图供应商适配为通用 ProviderChannel。

每个 provider = 一个通道(name 唯一, 作负载均衡成员名与审计 backend_provider)。
凭证由 credentials_resolver 每次实时解析(读 SERVICE_CONFIG, 支持热更新)。出错翻译成
ChannelError(retryable=True), 让通用 LoadBalancer 切下一家。vendor_model 即供应商侧模型名。
"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import dataclass
from collections.abc import Callable

import aiohttp

from .api import OpenAIImageError, size_for, generate_image
from ...core.types import NodeOutput
from ...core.request import GenerationRequest
from ....core.base.errors import ChannelError
from ....core.channels.channel import ProviderChannel


@dataclass(frozen=True)
class OpenAIImageCredentials:
    """一次凭证解析结果。"""

    enabled: bool = False
    api_key: str = ""
    base_url: str = ""


CredsResolver = Callable[[], OpenAIImageCredentials]


class OpenAIImageChannel(ProviderChannel):
    """一家 OpenAI 兼容生图供应商的通道。"""

    def __init__(self, name: str, *, credentials_resolver: CredsResolver, weight: int = 1) -> None:
        if not name:
            raise ValueError("OpenAIImageChannel 缺少 name")
        self.name = name
        self.weight = weight
        self._resolve = credentials_resolver

    async def check_available(self) -> bool:
        creds = self._resolve()
        return bool(creds.enabled and creds.api_key and creds.base_url)

    async def unavailable_reason(self) -> str:
        return f"供应商 {self.name} 未配置(需启用 + API Key + Base URL)"

    def audit_key_prefix(self) -> str:
        return (self._resolve().api_key or "")[:6]

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request: GenerationRequest = kwargs["request"]
        vendor_model: Optional[str] = kwargs.get("vendor_model")
        creds = self._resolve()
        if not creds.api_key or not creds.base_url:
            raise ChannelError(
                f"供应商 {self.name} 未配置 API Key/Base URL",
                retryable=True,
                channel=self.name,
                user_message="该供应商未配置完整凭证。",
            )
        model = vendor_model or str(request.params.get("model") or "")
        if not model:
            raise ChannelError(
                f"供应商 {self.name} 未指定 model",
                retryable=False,
                channel=self.name,
                user_message="未配置该供应商对应的模型名。",
            )

        size = size_for(request.ratio, request.width, request.height)
        try:
            data = await generate_image(
                base_url=creds.base_url,
                api_key=creds.api_key,
                model=model,
                prompt=request.prompt,
                image_list=request.images or None,
                size=size,
            )
        except OpenAIImageError as exc:
            # 429/503 是瞬时限流/过载:标 transient,run() 先在原通道退避重试一次
            raise ChannelError(
                str(exc),
                retryable=True,
                transient=exc.http_status in (429, 503),
                channel=self.name,
                user_message=exc.user_message,
            ) from exc
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise ChannelError(
                f"{self.name} 网络错误({type(exc).__name__}: {exc})",
                retryable=True,
                channel=self.name,
                user_message="网络异常,请稍后重试。",
            ) from exc

        output = NodeOutput(
            status="ok",
            output_type="image",
            data=data,
            mime_type="image/png",
            outputs={"image": data},
        )
        output.metadata.setdefault("channel", self.name)
        return output


__all__ = ["OpenAIImageChannel", "OpenAIImageCredentials", "CredsResolver"]
