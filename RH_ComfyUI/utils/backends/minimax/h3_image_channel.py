"""MiniMaxH3ImageChannel — 官方 MiniMax-H3-Image → 通用 ProviderChannel

凭证读 SERVICE_CONFIG 的 MiniMax_apikey;可用性还要求
MiniMax_Enabled_Models 勾选了 minimax_h3_image。该列表只关本官方通道,
不决定 host 模型 ``minimax_h3_image`` 整体是否可用(fal 等通道正交)。

官方接口是同步长连(POST /v1/images/generations | /edits 一次拿结果),
无任务态、无远端取消,supports_remote_cancel=False。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from collections.abc import Callable, Awaitable

import httpx

from gsuid_core.logger import logger

from .config import (
    MINIMAX_MODEL_H3_IMAGE,
    minimax_api_key,
    minimax_dry_run,
    minimax_disabled_reason,
    is_minimax_model_enabled,
)
from ...audit_key import mask_api_key
from ...core.types import NodeOutput, ProgressEvent
from ...core.request import GenerationRequest
from .h3_image_provider import H3ImageResult, MiniMaxH3ImageProvider, MiniMaxH3ImageProviderError
from ..seedance.provider import DryRunInterrupt
from ....core.base.errors import ChannelError, ValidationError
from ....core.channels.channel import ProviderChannel
from ....core.channels.failure import Phase, FailureKind


class MiniMaxH3ImageChannel(ProviderChannel):
    """官方 MiniMax H3-Image 通道(同步接口,无远端取消)。"""

    name = "minimax-h3-image"
    weight = 1
    accepts_model_field = True

    def __init__(
        self,
        *,
        weight: int = 1,
        credentials_resolver: Optional[Callable[[], tuple[str, bool]]] = None,
    ) -> None:
        self.weight = weight
        self._resolve = credentials_resolver or (lambda: (minimax_api_key(), minimax_dry_run()))
        self._cached: Optional[MiniMaxH3ImageProvider] = None

    def supports_remote_cancel(self) -> bool:
        return False

    def _get_provider(self) -> MiniMaxH3ImageProvider:
        api_key, dry_run = self._resolve()
        cached = self._cached
        if cached is not None:
            if cached.api_key != api_key or cached.dry_run != dry_run:
                cached.update_credentials(api_key=api_key, dry_run=dry_run)
                logger.info("[MiniMaxH3Image] 凭证已热更新")
            return cached
        provider = MiniMaxH3ImageProvider(api_key=api_key, dry_run=dry_run)
        self._cached = provider
        return provider

    async def check_available(self) -> bool:
        return bool(minimax_api_key() and is_minimax_model_enabled(MINIMAX_MODEL_H3_IMAGE))

    async def unavailable_reason(self) -> str:
        if not minimax_api_key():
            return "未配置 MiniMax API Key,请在 Web 控制台配置 MiniMax_apikey"
        if not is_minimax_model_enabled(MINIMAX_MODEL_H3_IMAGE):
            return "未启用 MiniMax H3-Image:请在「启用的 MiniMax 模型」中添加 minimax_h3_image"
        return "MiniMax H3-Image 通道不可用"

    def audit_key_prefix(self) -> str:
        return mask_api_key(minimax_api_key() or "")

    def supports_request(self, request: GenerationRequest) -> bool:
        try:
            self._get_provider().validate_request(request)
            return True
        except (MiniMaxH3ImageProviderError, ValidationError, TypeError, ValueError):
            return False

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request: GenerationRequest = kwargs["request"]
        on_progress = kwargs.get("on_progress")
        vendor_model: Optional[str] = kwargs.get("vendor_model") or MiniMaxH3ImageProvider.VENDOR_MODEL

        disabled = minimax_disabled_reason(MINIMAX_MODEL_H3_IMAGE, "MiniMax H3-Image")
        if disabled is not None:
            raise ChannelError(
                disabled,
                kind=FailureKind.CAPABILITY,
                phase=Phase.BEFORE_SEND,
                retryable=True,
                channel=self.name,
                user_message=disabled,
            )

        provider = self._get_provider()
        if not provider.api_key:
            raise ChannelError(
                "MiniMax H3-Image 缺少 API Key",
                kind=FailureKind.AUTH_OR_QUOTA,
                phase=Phase.BEFORE_SEND,
                retryable=True,
                channel=self.name,
                user_message="未配置 MiniMax API Key。",
            )

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("submitting", 5, "提交 MiniMax H3-Image 任务"))

        try:
            provider.validate_request(request)
        except MiniMaxH3ImageProviderError as exc:
            raise _to_channel_error(exc, self.name) from exc
        except ValidationError as exc:
            # 本通道表达不了的参数:换下一通道,不计健康
            raise ChannelError(
                f"{self.name} 通道不支持:{exc}",
                kind=FailureKind.CAPABILITY,
                phase=Phase.BEFORE_SEND,
                retryable=True,
                channel=self.name,
                user_message=str(exc),
            ) from exc

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("running", 30, "MiniMax H3-Image 生成中"))

        try:
            result = await provider.run(
                request,
                model=vendor_model,
                on_progress=_make_progress_cb(on_progress),
            )
        except DryRunInterrupt:
            raise
        except MiniMaxH3ImageProviderError as exc:
            raise _to_channel_error(exc, self.name) from exc
        except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
            raise ChannelError.from_transport(
                exc,
                stage="other",
                channel=self.name,
                message=f"MiniMax H3-Image 网络错误({type(exc).__name__}: {exc})",
                user_message="上游服务暂时不可用,请稍后重试。",
            ) from exc

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("done", 100, "MiniMax H3-Image 完成"))
        return self._to_output(result, vendor_model=result.vendor_model or vendor_model)

    def _to_output(self, result: H3ImageResult, *, vendor_model: Optional[str]) -> NodeOutput:
        model_id = vendor_model or MiniMaxH3ImageProvider.VENDOR_MODEL
        usage: dict[str, Any] = {
            "provider": self.name,
            "model": model_id,
            "request_id": result.request_id,
            "vendor_usage": dict(result.usage or {}),
        }
        return NodeOutput(
            status="ok",
            output_type="image",
            data=result.data,
            mime_type=result.mime_type,
            usage=usage,
            raw=result.raw,
            metadata={
                "task_id": result.request_id,
                "provider": self.name,
                "channel": self.name,
                "model": model_id,
            },
        )


def _to_channel_error(exc: MiniMaxH3ImageProviderError, channel: str) -> ChannelError:
    return ChannelError(
        str(exc),
        kind=exc.kind,
        phase=exc.phase,
        http_status=exc.http_status,
        retryable=exc.retryable,
        transient=exc.transient,
        channel=channel,
        code=exc.code or "",
        user_message=exc.user_message,
    )


_BUILTIN: Optional[dict[str, MiniMaxH3ImageChannel]] = None


def builtin_minimax_h3_image_channels() -> dict[str, MiniMaxH3ImageChannel]:
    global _BUILTIN
    if _BUILTIN is None:
        _BUILTIN = {"minimax-h3-image": MiniMaxH3ImageChannel()}
    return _BUILTIN


def _make_progress_cb(
    on_progress: Optional[Any],
) -> Callable[[H3ImageResult], Awaitable[None]]:
    async def _on_progress(_result: H3ImageResult) -> None:
        return

    return _on_progress


def _evt(stage: str, percent: float, message: str) -> ProgressEvent:
    return ProgressEvent(stage=stage, percent=percent, message=message)


async def _safe_emit(cb: Optional[Any], event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        result = cb(event)
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "MiniMaxH3ImageChannel",
    "builtin_minimax_h3_image_channels",
]
