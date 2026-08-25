"""HappyHorseChannel — DashScope HappyHorse → 通用 ProviderChannel

内置供应商 dashscope:凭证读 SERVICE_CONFIG 的 HappyHorse_* 键。
外部插件可通过构造 ``HappyHorseChannel(provider_cls=..., credentials_resolver=...)``
再 ``channel_registry.register_binding("happyhorse1.1", ch)`` 注入。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from collections.abc import Callable

import httpx

from gsuid_core.logger import logger

from .classify import classify_happyhorse, resolve_vendor_model
from .provider import HappyHorseProvider, HappyHorseProviderError
from ..http_retry import download_with_network_retry
from ...core.types import NodeOutput, ProgressEvent
from ...core.request import GenerationRequest
from ..dashscope.config import (
    DASHSCOPE_MODEL_HAPPYHORSE,
    ProviderCredentials,
    dashscope_credentials,
    dashscope_disabled_reason,
    is_dashscope_model_enabled,
)
from ..seedance.provider import DryRunInterrupt
from ....core.base.errors import ChannelError
from ....core.channels.channel import ProviderChannel

ConfigResolver = Callable[[], ProviderCredentials]
DryRunResolver = Callable[[], bool]


def service_config_credentials() -> ProviderCredentials:
    """读 RH_ComfyUI SERVICE_CONFIG 的 HappyHorse_* 键(与万相 3.0 共用)。"""
    return dashscope_credentials()


def _dry_run_enabled() -> bool:
    from ....rh_config.comfyui_config import plugin_dry_run

    return plugin_dry_run()


class HappyHorseChannel(ProviderChannel):
    """HappyHorse 通道:包装任意 ``HappyHorseProvider`` 子类。

    Args:
        provider_cls: 供应商实现类(默认官方 DashScope ``HappyHorseProvider``;
            外部插件可注入自己的子类)。
        weight: 负载均衡权重。
        credentials_resolver: 凭证回调;默认读宿主 ``HappyHorse_*`` 配置。
        name: 通道名;默认取 ``provider_cls.name``。
        dry_run_resolver: Dry-Run 开关;默认读 ``PLUGIN_CONFIG.Dry_Run``。
            外部插件可注入自己的 resolver。
    """

    name = "dashscope"
    weight = 1
    accepts_model_field = True

    def __init__(
        self,
        provider_cls: type[HappyHorseProvider] = HappyHorseProvider,
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
        self._resolve_creds: ConfigResolver = credentials_resolver or service_config_credentials
        self._resolve_dry_run: DryRunResolver = dry_run_resolver or _dry_run_enabled
        self._cached: Optional[HappyHorseProvider] = None

    def supports_remote_cancel(self) -> bool:
        """DashScope / 网关 HappyHorse 均有任务 cancel/DELETE。"""
        return True

    def credentials(self) -> ProviderCredentials:
        return self._resolve_creds()

    def get_provider_for_resume(self) -> Optional[HappyHorseProvider]:
        """公开:resume_poll 取 provider。"""
        return self._get_provider()

    def _get_provider(self) -> Optional[HappyHorseProvider]:
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
                logger.info(f"[HappyHorse] 供应商 {self.name} 凭证已热更新")
            return cached

        provider = self._provider_cls(
            api_key=creds.api_key,
            base_url=base_url or None,
            dry_run=dry_run,
        )
        if not creds.api_key:
            logger.warning(f"[HappyHorse] 供应商 {self.name} API Key 为空")
        self._cached = provider
        return provider

    async def check_available(self) -> bool:
        creds = self._resolve_creds()
        return bool(
            creds.enabled
            and creds.api_key
            and creds.base_url
            and is_dashscope_model_enabled(DASHSCOPE_MODEL_HAPPYHORSE)
        )

    async def unavailable_reason(self) -> str:
        disabled = dashscope_disabled_reason(DASHSCOPE_MODEL_HAPPYHORSE, "HappyHorse 1.1")
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
            spec = classify_happyhorse(request)
            return provider.can_handle_spec(spec)
        except Exception:
            return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request: GenerationRequest = kwargs["request"]
        on_progress = kwargs.get("on_progress")
        vendor_model: Optional[str] = kwargs.get("vendor_model")

        disabled = dashscope_disabled_reason(DASHSCOPE_MODEL_HAPPYHORSE, "HappyHorse 1.1")
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

        spec = classify_happyhorse(request)
        # 未显式注入 vendor_model 时按形态自动解析
        model_id = resolve_vendor_model(spec.shape, override=vendor_model or None)
        # 节点级 backend_model 若是逻辑名 happyhorse1.1,忽略,仍走自动解析
        if model_id in ("happyhorse1.1", "happyhorse", "happyhorse1", ""):
            model_id = resolve_vendor_model(spec.shape)

        if on_progress is not None:
            await _safe_emit(
                on_progress,
                _evt("submitting", 5, f"提交 HappyHorse({self.name}/{model_id})"),
            )

        try:
            final = await provider.run(
                spec,
                model=model_id,
                on_progress=_make_progress_cb(on_progress, self.name),
            )
        except DryRunInterrupt:
            raise
        except HappyHorseProviderError as exc:
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
            await _safe_emit(on_progress, _evt("done", 100, "HappyHorse 完成"))

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


_BUILTIN_CHANNELS: Optional[dict[str, HappyHorseChannel]] = None


def builtin_happyhorse_channels() -> dict[str, HappyHorseChannel]:
    global _BUILTIN_CHANNELS
    if _BUILTIN_CHANNELS is None:
        _BUILTIN_CHANNELS = {
            "dashscope": HappyHorseChannel(weight=1),
        }
    return _BUILTIN_CHANNELS


def _make_progress_cb(on_progress: Optional[Any], provider_name: Optional[str] = None):
    async def _on_progress(task: Any) -> None:
        if on_progress is None:
            return
        status = getattr(getattr(task, "status", None), "value", None) or ""
        if status == "queued":
            await _safe_emit(on_progress, _evt("queued", 25, f"排队中({provider_name or 'hh'})"))
        elif status == "running":
            await _safe_emit(on_progress, _evt("running", 55, f"HappyHorse 生成中({provider_name or 'hh'})"))

    return _on_progress


async def _download(
    url: str,
    *,
    timeout: float = 300.0,
    max_retries: int = 5,
    initial_backoff: float = 5.0,
) -> bytes:
    return await download_with_network_retry(
        url,
        timeout=timeout,
        attempts=max_retries,
        wait_s=initial_backoff,
        label="HappyHorse",
    )


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
    "ProviderCredentials",
    "HappyHorseChannel",
    "service_config_credentials",
    "builtin_happyhorse_channels",
]
