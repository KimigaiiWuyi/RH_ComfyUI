"""MiniMaxH3Channel — 官方 MiniMax-H3 → 通用 ProviderChannel

凭证读 SERVICE_CONFIG 的 MiniMax_apikey;可用性还要求
MiniMax_Enabled_Models 勾选了 minimax_h3。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from collections.abc import Callable, Awaitable

import httpx

from gsuid_core.logger import logger

from .config import (
    MINIMAX_MODEL_H3,
    minimax_api_key,
    minimax_dry_run,
    minimax_disabled_reason,
    is_minimax_model_enabled,
)
from .h3_classify import classify_minimax_h3
from .h3_provider import MiniMaxH3Provider, MiniMaxH3ProviderError
from ...core.types import NodeOutput, ProgressEvent
from ...core.request import GenerationRequest
from ..seedance.provider import NormalizedTask, DryRunInterrupt
from ....core.base.errors import ChannelError
from ....core.channels.channel import ProviderChannel


class MiniMaxH3Channel(ProviderChannel):
    """官方 MiniMax H3 通道。"""

    name = "minimax-h3"
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
        self._cached: Optional[MiniMaxH3Provider] = None

    def supports_remote_cancel(self) -> bool:
        return True

    def _get_provider(self) -> MiniMaxH3Provider:
        api_key, dry_run = self._resolve()
        cached = self._cached
        if cached is not None:
            if cached.api_key != api_key or cached.dry_run != dry_run:
                cached.update_credentials(api_key=api_key, dry_run=dry_run)
                logger.info("[MiniMaxH3] 凭证已热更新")
            return cached
        provider = MiniMaxH3Provider(api_key=api_key, dry_run=dry_run)
        self._cached = provider
        return provider

    def get_provider_for_resume(self) -> Optional[MiniMaxH3Provider]:
        return self._get_provider()

    async def check_available(self) -> bool:
        return bool(minimax_api_key() and is_minimax_model_enabled(MINIMAX_MODEL_H3))

    async def unavailable_reason(self) -> str:
        if not minimax_api_key():
            return "未配置 MiniMax API Key,请在 Web 控制台配置 MiniMax_apikey"
        if not is_minimax_model_enabled(MINIMAX_MODEL_H3):
            return "未启用 MiniMax H3:请在「启用的 MiniMax 模型」中添加 minimax_h3"
        return "MiniMax H3 通道不可用"

    def audit_key_prefix(self) -> str:
        return (minimax_api_key() or "")[:6]

    def supports_request(self, request: GenerationRequest) -> bool:
        try:
            spec = classify_minimax_h3(request)
            return self._get_provider().can_handle_spec(spec)
        except (TypeError, ValueError, KeyError):
            return False

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request: GenerationRequest = kwargs["request"]
        on_progress = kwargs.get("on_progress")
        vendor_model: Optional[str] = kwargs.get("vendor_model") or MiniMaxH3Provider.VENDOR_MODEL

        disabled = minimax_disabled_reason(MINIMAX_MODEL_H3, "MiniMax H3")
        if disabled is not None:
            raise ChannelError(
                disabled,
                retryable=True,
                channel=self.name,
                user_message=disabled,
            )

        provider = self._get_provider()
        if not provider.api_key:
            raise ChannelError(
                "MiniMax H3 缺少 API Key",
                retryable=True,
                channel=self.name,
                user_message="未配置 MiniMax API Key。",
            )

        spec = classify_minimax_h3(request)
        if on_progress is not None:
            await _safe_emit(on_progress, _evt("submitting", 5, "提交 MiniMax H3 任务"))

        try:
            final = await provider.run(
                spec,
                model=vendor_model,
                on_progress=_make_progress_cb(on_progress),
            )
        except DryRunInterrupt:
            raise
        except MiniMaxH3ProviderError as exc:
            raise ChannelError(
                str(exc),
                retryable=exc.retryable,
                transient=bool(exc.retryable and exc.http_status in (429, 503, 529)),
                channel=self.name,
                code=exc.code or "",
                user_message=exc.user_message,
            ) from exc
        except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
            raise ChannelError(
                f"MiniMax H3 网络错误({type(exc).__name__}: {exc})",
                retryable=True,
                channel=self.name,
                code="PROVIDER_NETWORK_ERROR",
                user_message="上游服务暂时不可用,请稍后重试。",
            ) from exc

        if not final.video_url:
            raise ChannelError(
                f"MiniMax H3 任务成功但未返回 video url: {final.raw}",
                retryable=True,
                channel=self.name,
                user_message="上游未返回视频结果,请稍后重试。",
            )

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("downloading", 90, "下载视频"))
        try:
            from ..seedance.channel import _download

            video = await _download(final.video_url)
        except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
            raise ChannelError(
                f"MiniMax H3 下载生成结果失败({type(exc).__name__}: {exc})",
                retryable=False,
                channel=self.name,
                code="RESULT_DOWNLOAD_FAILED",
                user_message="视频已生成但下载失败,请稍后重试。",
            ) from exc

        usage: dict[str, Any] = dict(final.usage or {})
        usage["task_id"] = final.id
        usage["provider"] = self.name
        usage["model"] = vendor_model

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("done", 100, "MiniMax H3 完成"))

        return NodeOutput(
            status="ok",
            output_type="video",
            data=video,
            mime_type="video/mp4",
            usage=usage,
            raw=final.raw,
            metadata={
                "task_id": final.id,
                "provider": self.name,
                "channel": self.name,
                "model": vendor_model,
                "shape": spec.shape.value,
            },
        )


_BUILTIN: Optional[dict[str, MiniMaxH3Channel]] = None


def builtin_minimax_h3_channels() -> dict[str, MiniMaxH3Channel]:
    global _BUILTIN
    if _BUILTIN is None:
        _BUILTIN = {"minimax-h3": MiniMaxH3Channel()}
    return _BUILTIN


def _make_progress_cb(
    on_progress: Optional[Any],
) -> Callable[[NormalizedTask], Awaitable[None]]:
    async def _on_progress(task: NormalizedTask) -> None:
        if on_progress is None:
            return
        status = task.status.value
        if status == "queued":
            await _safe_emit(on_progress, _evt("queued", 25, "MiniMax H3 排队中"))
        elif status == "running":
            await _safe_emit(on_progress, _evt("running", 55, "MiniMax H3 生成中"))

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
    "MiniMaxH3Channel",
    "builtin_minimax_h3_channels",
]
