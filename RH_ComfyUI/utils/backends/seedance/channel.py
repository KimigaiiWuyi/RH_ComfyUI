"""SeedanceProviderChannel — 把一个 SeedanceProvider 适配为通用 ProviderChannel

2026-07 起 Seedance 的多供应商不再由后端内部自建负载均衡,而是每家供应商
= 一个 ``ProviderChannel``,由通用 ``LoadBalancer`` 统一排序 / 熔断 / 故障切换
(见 core.base.generation.run())。

- 内置供应商(ark / runninghub):``builtin_seedance_channels()`` 构造,凭证读
  RH_ComfyUI 自己的 SERVICE_CONFIG;
- 外部供应商:外部插件用自己的凭证回调构造 SeedanceProviderChannel,
  经 ``channel_registry.register_binding()`` 注入宿主模型的候选列表。

Provider 的 render/parse/poll 细粒度机制(provider.py)完全复用;本类只负责
凭证热更新、形态分类、下载、异常翻译(SeedanceProviderError → ChannelError)。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from dataclasses import dataclass
from collections.abc import Callable

import httpx

from gsuid_core.logger import logger

from .spec import VideoGenSpec
from .classify import classify_video_spec
from .provider import (
    NormalizedTask,
    DryRunInterrupt,
    SeedanceProvider,
    SeedanceProviderError,
    UnsupportedProviderShapeError,
)
from ...core.types import NodeOutput, ProgressEvent
from ...core.request import GenerationRequest
from ....core.base.errors import ChannelError
from ....core.channels.channel import ProviderChannel

# ── 凭证解析 ──


@dataclass(frozen=True)
class ProviderCredentials:
    """一次凭证解析的结果(由 credentials_resolver 返回)"""

    enabled: bool = False
    api_key: str = ""
    base_url: Optional[str] = None


ConfigResolver = Callable[[], ProviderCredentials]


def service_config_credentials(name: str, default_base_url: str) -> ProviderCredentials:
    """内置供应商的默认凭证方案:读 RH_ComfyUI 自己的 SERVICE_CONFIG

    键名约定: Seedance_Enable_{name} / Seedance_apikey_{name} / Seedance_BaseURL_{name};
    runninghub 的 key 为空时回退 RH_apikey。
    """
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    def _get(key: str) -> Any:
        try:
            return SERVICE_CONFIG.get_config(key).data
        except Exception:
            return None

    enabled = bool(_get(f"Seedance_Enable_{name}"))
    api_key = str(_get(f"Seedance_apikey_{name}") or "")
    if name == "runninghub" and not api_key:
        api_key = str(_get("RH_apikey") or "")
    base_url = str(_get(f"Seedance_BaseURL_{name}") or "").strip() or None
    return ProviderCredentials(enabled=enabled, api_key=api_key, base_url=base_url or (default_base_url or None))


def _dry_run_enabled() -> bool:
    from ....rh_config.comfyui_config import plugin_dry_run

    return plugin_dry_run()


# ── 通道 ──


class SeedanceProviderChannel(ProviderChannel):
    """把一个 SeedanceProvider 子类适配为通用 ProviderChannel。

    Args:
        provider_cls:        SeedanceProvider 子类(ark / runninghub / 网关 等)
        weight:              weighted 负载均衡权重
        credentials_resolver: 凭证解析回调,返回 ProviderCredentials
                             (可来自任意插件自己的配置面板)
        accepts_model_field: 创建请求体是否接受 model 字段
                             (RunningHub 端点即模型,为 False)
    """

    def __init__(
        self,
        provider_cls: type[SeedanceProvider],
        *,
        weight: int = 1,
        credentials_resolver: Optional[ConfigResolver] = None,
        accepts_model_field: bool = True,
        name: Optional[str] = None,
    ) -> None:
        if not provider_cls.name and not name:
            raise ValueError(f"供应商类 {provider_cls.__qualname__} 缺少 name 属性")
        # 显式 name 优先(网关 slot 通道与 vendor_channel 对齐)
        self.name = (name or provider_cls.name or "").strip()
        if not self.name:
            raise ValueError(f"供应商类 {provider_cls.__qualname__} 缺少 name 属性")
        self.weight = weight
        self.accepts_model_field = accepts_model_field
        self._provider_cls = provider_cls
        self._resolve_creds: ConfigResolver = credentials_resolver or (
            lambda: service_config_credentials(provider_cls.name, provider_cls.DEFAULT_BASE_URL)
        )
        self._cached: Optional[SeedanceProvider] = None

    # ── 凭证 / 实例 ──

    def credentials(self) -> ProviderCredentials:
        return self._resolve_creds()

    def _get_provider(self) -> Optional[SeedanceProvider]:
        """获取或创建 provider 实例(带缓存 + 凭证热更新)。"""
        creds = self._resolve_creds()
        if not creds.base_url:
            logger.warning(f"[Seedance] 供应商 {self.name} 无可用 Base URL,请求将失败。")

        dry_run = _dry_run_enabled()
        cached = self._cached
        if cached is not None:
            old_key, old_url = cached.api_key, cached.base_url
            if old_key != creds.api_key or old_url != creds.base_url or cached.dry_run != dry_run:
                cached.update_credentials(api_key=creds.api_key, base_url=creds.base_url, dry_run=dry_run)
                logger.info(
                    f"[Seedance] 供应商 {self.name} 凭证已热更新 "
                    f"(key_changed={old_key != creds.api_key}, url_changed={old_url != creds.base_url})"
                )
            return cached

        provider = self._provider_cls(api_key=creds.api_key, base_url=creds.base_url, dry_run=dry_run)
        if not creds.api_key:
            logger.warning(f"[Seedance] 供应商 {self.name} 实例化时 API Key 为空,请求将被跳过。")
        self._cached = provider
        return provider

    def get_provider_for_resume(self) -> Optional[SeedanceProvider]:
        """公开:resume_poll 取 provider,避免调用方依赖 ``_get_provider``。"""
        return self._get_provider()

    # ── ProviderChannel 契约 ──

    async def check_available(self) -> bool:
        creds = self._resolve_creds()
        return bool(creds.enabled and creds.api_key and creds.base_url)

    async def unavailable_reason(self) -> str:
        return f"供应商 {self.name} 未配置(需要启用开关 + API Key + Base URL)"

    def audit_key_prefix(self) -> str:
        return (self._resolve_creds().api_key or "")[:6]

    def supports_remote_cancel(self) -> bool:
        """跟 provider 是否 override 了非 no-op ``delete``(ark/网关 True,runninghub False)。"""
        from .provider import SeedanceProvider

        return self._provider_cls.delete is not SeedanceProvider.delete

    def supports_request(self, request: GenerationRequest) -> bool:
        """能力预检:不发起 HTTP,仅复用 provider.can_handle_spec()。

        复用 ``_get_provider()`` 的缓存(provider 实例化有 credentials
        解析开销,但 ``self._cached`` 类实例级别缓存,二次调用零 IO)。

        错误兜底策略:任何异常一律返回 True —— 分类失败 / Provider 未
        实例化 / 能力字段意外等边角情况,留给 ``invoke()`` 自己暴露;
        这里保守"放行"避免误杀可救活的请求(比如凭证切换中短暂返回 None)。

        故意**不**检查媒体数:can_handle_spec 自身已省略该维度,
        多图多视频场景即便某家通道 max_images=2 也可能由别家承接,
        不该在路由阶段就砍掉整条候选线。
        """
        try:
            provider = self._get_provider()
            if provider is None:
                return True
            spec = classify_video_spec(request)
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

        spec: VideoGenSpec = classify_video_spec(request)
        spec.params.setdefault("draft", request.params.get("draft"))
        # 请求者身份透传(供应商旁路):需要按用户做归属/计量的 Provider
        # 从 spec.params["user_id"] 取(HTTP 入口 = str(account.id))
        spec.params.setdefault("user_id", request.user_id or "")

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("submitting", 5, f"提交 Seedance({self.name}) 任务"))

        try:
            final = await provider.run(spec, model=vendor_model, on_progress=_make_progress_cb(on_progress, self.name))
        except UnsupportedProviderShapeError as exc:
            # 形态/参数不被该供应商支持 → 不可重试(换通道也解决不了同一形态限制)
            raise ChannelError(
                f"供应商 {self.name} 不支持该任务: {exc}",
                retryable=False,
                channel=self.name,
                code=exc.code or "",
                user_message=exc.user_message,
            ) from exc
        except DryRunInterrupt:
            # Dry-Run:继承 BaseException,不会被通用 run() 的 except ChannelError 捕获,
            # 直接透传终止,不触发熔断 / 切换。
            raise
        except SeedanceProviderError as exc:
            # retryable 尊重供应商侧标注(HTTP 状态/网关 data.retryable/raise 点语义):
            # 参数类失败(4xx/VID-*)换通道也解决不了,不再盲目 failover 烧钱;
            # 429/503/421(RunningHub 并发满)额外标 transient,run() 先在原通道退避重试一次
            queue_full = (exc.http_status in (421, 415)) or (
                (exc.code or "").upper() in {"TASK_QUEUE_MAXED", "TASK_INSTANCE_MAXED"}
            )
            raise ChannelError(
                str(exc),
                retryable=exc.retryable,
                transient=exc.retryable and (exc.http_status in (429, 503, 415, 421) or queue_full),
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
                user_message=_user_msg_for_network_error(exc),
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
            # 生成已成功、只是取件失败:换通道会重新生成(重复烧钱),
            # 故 retryable=False,翻译成干净文案而非裸 httpx traceback
            raise ChannelError(
                f"{self.name} 下载生成结果失败({type(exc).__name__}: {exc})",
                retryable=False,
                channel=self.name,
                code="RESULT_DOWNLOAD_FAILED",
                user_message="视频已生成但下载失败,请稍后重试。",
            ) from exc

        outputs: dict[str, Any] = {}
        if final.last_frame_url:
            try:
                outputs["last_frame"] = await _download(final.last_frame_url, timeout=120)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[Seedance] 下载尾帧失败: {exc}")

        usage: dict[str, Any] = dict(final.usage or {})
        usage["task_id"] = final.id
        usage["provider"] = self.name
        if vendor_model:
            usage["model"] = vendor_model

        if on_progress is not None:
            await _safe_emit(on_progress, _evt("done", 100, "Seedance 完成"))

        out_fmt = (spec.output_format or "mp4").strip().lower()
        mime = "video/quicktime" if out_fmt == "mov" else "video/mp4"

        return NodeOutput(
            status="ok",
            output_type="video",
            data=video,
            mime_type=mime,
            outputs=outputs,
            usage=usage,
            raw=final.raw,
            metadata={
                "task_id": final.id,
                "provider": self.name,
                "channel": self.name,
                "model": vendor_model,
                "shape": spec.shape.value,
                "output_format": out_fmt,
            },
        )


# ── 内置供应商通道(进程级单例;跨模型共享实例,便于统一凭证缓存) ──

_BUILTIN_CHANNELS: Optional[dict[str, SeedanceProviderChannel]] = None


def builtin_seedance_channels() -> dict[str, SeedanceProviderChannel]:
    """内置供应商 → 通道实例(ark / runninghub)。"""
    global _BUILTIN_CHANNELS
    if _BUILTIN_CHANNELS is None:
        from .providers import ArkSeedanceProvider, RunningHubSeedanceProvider

        _BUILTIN_CHANNELS = {
            "ark": SeedanceProviderChannel(ArkSeedanceProvider, weight=3),
            "runninghub": SeedanceProviderChannel(RunningHubSeedanceProvider, weight=1, accepts_model_field=False),
        }
    return _BUILTIN_CHANNELS


# ── helpers(自 executor.py 迁入) ──


def _make_progress_cb(on_progress: Optional[Any], provider_name: Optional[str] = None):
    async def _on_progress(task: NormalizedTask) -> None:
        if on_progress is None:
            return
        if task.status.value == "queued":
            await _safe_emit(on_progress, _evt("queued", 25, f"排队中({provider_name or 'unknown'})"))
        elif task.status.value == "running":
            await _safe_emit(on_progress, _evt("running", 55, f"Seedance 生成中({provider_name or 'unknown'})"))
        if task.status.value == "succeeded":
            await _safe_emit(on_progress, _evt("downloading", 90, "下载视频"))

    return _on_progress


async def _download(
    url: str,
    *,
    timeout: float = 300.0,
    max_retries: int = 3,
    initial_backoff: float = 1.0,
) -> bytes:
    """下载 URL 内容,失败时按指数退避重试(CDN 临时链接抖动是常态)。"""
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
                    f"[Seedance] 下载失败({type(last_exc).__name__}: {last_exc}),"
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


def _user_msg_for_network_error(exc: BaseException) -> str:
    """裸 httpx/asyncio/OSError → 友好 user_message。"""
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "网络超时,请稍后重试。"
    if isinstance(exc, httpx.TimeoutException):
        return "网络超时,请稍后重试。"
    if isinstance(exc, httpx.ConnectError):
        return "无法连接到供应商服务,请检查网络后重试。"
    if isinstance(exc, httpx.NetworkError):
        return "网络异常,请稍后重试。"
    if isinstance(exc, asyncio.TimeoutError):
        return "任务等待超时,请稍后重试。"
    if isinstance(exc, OSError):
        return "底层网络异常,请稍后重试。"
    return "上游服务暂时不可用,请稍后重试。"


__all__ = [
    "ProviderCredentials",
    "SeedanceProviderChannel",
    "service_config_credentials",
    "builtin_seedance_channels",
]
