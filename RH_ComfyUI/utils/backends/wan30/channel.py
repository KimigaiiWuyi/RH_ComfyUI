"""Wan30Channel — DashScope 万相 3.0 → 通用 ProviderChannel

凭证复用 HappyHorse_*_dashscope;可用性还要求
DashScope_Enabled_Models 勾选了 wan3.0。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from collections.abc import Callable

import httpx

from gsuid_core.logger import logger

from .classify import VENDOR_MODEL, classify_wan30
from .provider import Wan30Provider, Wan30ProviderError
from ...core.types import NodeOutput
from ...core.request import GenerationRequest
from ..dashscope.config import (
    DASHSCOPE_MODEL_WAN30,
    ProviderCredentials,
    dashscope_credentials,
    dashscope_disabled_reason,
    is_dashscope_model_enabled,
)
from ..seedance.provider import DryRunInterrupt
from ....core.base.errors import ChannelError
from ..happyhorse.channel import _evt, _download, _safe_emit, _dry_run_enabled
from ....core.channels.channel import ProviderChannel

ConfigResolver = Callable[[], ProviderCredentials]
DryRunResolver = Callable[[], bool]


class Wan30Channel(ProviderChannel):
    """万相 3.0 通道,与 HappyHorse 共用 dashscope 通道名与凭证。"""

    name = "dashscope"
    weight = 1
    accepts_model_field = True

    def __init__(
        self,
        provider_cls: type[Wan30Provider] = Wan30Provider,
        *,
        weight: int = 1,
        credentials_resolver: Optional[ConfigResolver] = None,
        name: Optional[str] = None,
        dry_run_resolver: Optional[DryRunResolver] = None,
    ) -> None:
        if not getattr(provider_cls, "name", None):
            raise ValueError(f"供应商类 {provider_cls.__qualname__} 缺少 name 属性")
        self._provider_cls = provider_cls
        self.name = name or provider_cls.name
        self.weight = weight
        self._resolve_creds: ConfigResolver = credentials_resolver or dashscope_credentials
        self._resolve_dry_run: DryRunResolver = dry_run_resolver or _dry_run_enabled
        self._cached: Optional[Wan30Provider] = None

    def supports_remote_cancel(self) -> bool:
        return True

    def credentials(self) -> ProviderCredentials:
        return self._resolve_creds()

    def get_provider_for_resume(self) -> Optional[Wan30Provider]:
        return self._get_provider()

    def _get_provider(self) -> Optional[Wan30Provider]:
        creds = self._resolve_creds()
        dry_run = bool(self._resolve_dry_run())
        default_base = self._provider_cls.DEFAULT_BASE_URL or ""
        base_url = (creds.base_url or default_base or "").rstrip("/") or default_base
        cached = self._cached
        if cached is not None:
            if cached.api_key != creds.api_key or cached.base_url != base_url or cached.dry_run != dry_run:
                cached.update_credentials(
                    api_key=creds.api_key,
                    base_url=base_url or None,
                    dry_run=dry_run,
                )
                logger.info(f"[Wan30] 供应商 {self.name} 凭证已热更新")
            return cached

        provider = self._provider_cls(
            api_key=creds.api_key,
            base_url=base_url or None,
            dry_run=dry_run,
        )
        if not creds.api_key:
            logger.warning(f"[Wan30] 供应商 {self.name} API Key 为空")
        self._cached = provider
        return provider

    async def check_available(self) -> bool:
        creds = self._resolve_creds()
        return bool(
            creds.enabled
            and creds.api_key
            and creds.base_url
            and is_dashscope_model_enabled(DASHSCOPE_MODEL_WAN30)
        )

    async def unavailable_reason(self) -> str:
        disabled = dashscope_disabled_reason(DASHSCOPE_MODEL_WAN30, "万相 3.0")
        if disabled is not None:
            return disabled
        return f"供应商 {self.name} 未配置(需要启用开关 + API Key + Base URL)"

    def audit_key_prefix(self) -> str:
        return (self._resolve_creds().api_key or "")[:6]

    def supports_request(self, request: GenerationRequest) -> bool:
        try:
            provider = self._get_provider()
            if provider is None:
                return True
            spec = classify_wan30(request)
            return provider.can_handle_spec(spec)
        except Exception:
            return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request: GenerationRequest = kwargs["request"]
        on_progress = kwargs.get("on_progress")
        vendor_model: Optional[str] = kwargs.get("vendor_model") or VENDOR_MODEL

        disabled = dashscope_disabled_reason(DASHSCOPE_MODEL_WAN30, "万相 3.0")
        if disabled is not None:
            raise ChannelError(
                disabled,
                retryable=True,
                channel=self.name,
                user_message=disabled,
            )

        provider = self._get_provider()
        if provider is None or not provider.api_key:
            raise ChannelError(
                f"供应商 {self.name} 缺少 API Key",
                retryable=True,
                channel=self.name,
                user_message="该供应商未配置 API Key。",
            )

        spec = classify_wan30(request)
        model_id = (vendor_model or "").strip() or VENDOR_MODEL
        if model_id in ("wan3.0", "wan30", "wan3", ""):
            model_id = VENDOR_MODEL

        if on_progress is not None:
            await _safe_emit(
                on_progress,
                _evt("submitting", 5, f"提交万相 3.0({self.name}/{model_id})"),
            )

        try:
            final = await provider.run(
                spec,
                model=model_id,
                on_progress=_make_progress_cb(on_progress, self.name),
            )
        except DryRunInterrupt:
            raise
        except Wan30ProviderError as exc:
            raise ChannelError(
                str(exc),
                retryable=exc.retryable,
                transient=exc.retryable and exc.http_status in (429, 503),
                channel=self.name,
                code=exc.code or "",
                user_message=exc.user_message,
            ) from exc
        except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
            raise ChannelError(
                f"{self.name} 网络错误({type(exc).__name__}: {exc})",
                retryable=True,
                channel=self.name,
                code="PROVIDER_NETWORK_ERROR",
                user_message="上游服务暂时不可用,请稍后重试。",
            ) from exc

        if not final.video_url:
            raise ChannelError(
                f"{self.name} 任务成功但未返回 video_url: {final.raw}",
                retryable=True,
                channel=self.name,
                user_message="上游未返回视频结果,请稍后重试。",
            )

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("downloading", 90, "下载视频"))
        try:
            video = await _download(final.video_url)
        except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
            raise ChannelError(
                f"{self.name} 下载生成结果失败({type(exc).__name__}: {exc})",
                retryable=False,
                channel=self.name,
                code="RESULT_DOWNLOAD_FAILED",
                user_message="视频已生成但下载失败,请稍后重试。",
            ) from exc

        usage: dict[str, Any] = dict(final.usage or {})
        usage["task_id"] = final.id
        usage["provider"] = self.name
        usage["model"] = model_id

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("done", 100, "万相 3.0 完成"))

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
                "model": model_id,
                "shape": spec.shape.value,
            },
        )


_BUILTIN_CHANNELS: Optional[dict[str, Wan30Channel]] = None


def builtin_wan30_channels() -> dict[str, Wan30Channel]:
    global _BUILTIN_CHANNELS
    if _BUILTIN_CHANNELS is None:
        _BUILTIN_CHANNELS = {
            "dashscope": Wan30Channel(weight=1),
        }
    return _BUILTIN_CHANNELS


def _make_progress_cb(on_progress: Optional[Any], provider_name: Optional[str] = None):
    async def _on_progress(task: Any) -> None:
        if on_progress is None:
            return
        status = getattr(getattr(task, "status", None), "value", None) or ""
        if status == "queued":
            await _safe_emit(on_progress, _evt("queued", 25, f"排队中({provider_name or 'wan30'})"))
        elif status == "running":
            await _safe_emit(on_progress, _evt("running", 55, f"万相 3.0 生成中({provider_name or 'wan30'})"))

    return _on_progress


__all__ = [
    "Wan30Channel",
    "builtin_wan30_channels",
]
