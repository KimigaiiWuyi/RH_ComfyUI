"""HappyHorseChannel — DashScope HappyHorse → 通用 ProviderChannel

内置供应商 dashscope:凭证读 SERVICE_CONFIG 的 HappyHorse_* 键。
外部插件可通过构造 ``HappyHorseChannel(provider_cls=..., credentials_resolver=...)``
再 ``channel_registry.register_binding("happyhorse1.1", ch)`` 注入。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from dataclasses import dataclass
from collections.abc import Callable

import httpx

from gsuid_core.logger import logger

from .classify import classify_happyhorse, resolve_vendor_model
from .provider import HappyHorseProvider, HappyHorseProviderError
from ...core.types import NodeOutput, ProgressEvent
from ...core.request import GenerationRequest
from ..seedance.provider import DryRunInterrupt
from ....core.base.errors import ChannelError
from ....core.channels.channel import ProviderChannel


@dataclass(frozen=True)
class ProviderCredentials:
    enabled: bool = False
    api_key: str = ""
    base_url: Optional[str] = None


ConfigResolver = Callable[[], ProviderCredentials]
DryRunResolver = Callable[[], bool]


def service_config_credentials() -> ProviderCredentials:
    """读 RH_ComfyUI SERVICE_CONFIG 的 HappyHorse_* 键。"""
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    def _get(key: str) -> Any:
        try:
            return SERVICE_CONFIG.get_config(key).data
        except Exception:
            return None

    enabled = bool(_get("HappyHorse_Enable_dashscope"))
    api_key = str(_get("HappyHorse_apikey_dashscope") or "")
    base_url = str(_get("HappyHorse_BaseURL_dashscope") or "").strip() or None
    return ProviderCredentials(
        enabled=enabled,
        api_key=api_key,
        base_url=base_url or HappyHorseProvider.DEFAULT_BASE_URL,
    )


def _dry_run_enabled() -> bool:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    try:
        return bool(SERVICE_CONFIG.get_config("HappyHorse_Dry_Run").data)
    except Exception:
        return False


class HappyHorseChannel(ProviderChannel):
    """HappyHorse 通道:包装任意 ``HappyHorseProvider`` 子类。

    Args:
        provider_cls: 供应商实现类(默认官方 DashScope ``HappyHorseProvider``;
            外部聚合网关可注入自己的子类)。
        weight: 负载均衡权重。
        credentials_resolver: 凭证回调;默认读宿主 ``HappyHorse_*`` 配置。
        name: 通道名;默认取 ``provider_cls.name``。
        dry_run_resolver: Dry-Run 开关;默认读宿主 ``HappyHorse_Dry_Run``。
            外部插件应用自己的开关,避免误读宿主 DashScope Dry-Run。
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
        return bool(creds.enabled and creds.api_key and creds.base_url)

    async def unavailable_reason(self) -> str:
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
    max_retries: int = 3,
    initial_backoff: float = 1.0,
) -> bytes:
    last_exc: Optional[BaseException] = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise
                last_exc = exc
            except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
                last_exc = exc
            if attempt < max_retries - 1:
                backoff = initial_backoff * (2**attempt)
                logger.warning(
                    f"[HappyHorse] 下载失败({type(last_exc).__name__}: {last_exc}),"
                    f" {attempt + 1}/{max_retries} 次重试,等待 {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
    assert last_exc is not None
    raise last_exc


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
