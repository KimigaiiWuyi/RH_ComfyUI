"""Seedance Adapter — 多供应商视频生成后端(负载均衡 + 自动切换)

设计要点:
- 唯一的执行主干:classify → provider.run → 下载 → NodeOutput
- 供应商选择:候选列表模式,支持 round_robin / weighted / least_failures 策略
- 自动切换:provider 调用失败(可重试错误)时自动降级到下一个供应商
- 熔断机制:连续失败超过阈值后,暂时跳过该供应商一段时间
- 上层(命令/路由/执行器)对供应商与任务形态完全无感
- key 复用:runninghub 时优先 `Seedance_apikey_runninghub`,为空回退 `RH_apikey`
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from gsuid_core.logger import logger

from .spec import VideoGenSpec
from ..base import Adapter
from .classify import classify_video_spec
from .provider import (
    NormalizedTask,
    DryRunInterrupt,
    SeedanceProvider,
    SeedanceProviderError,
    UnsupportedProviderShapeError,
)
from .registry import (
    get_provider,
    vendor_model_for,
    get_registration,
    order_candidates,
    get_failure_count,
    registered_providers,
    record_provider_failure,
    record_provider_success,
)
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationRequest
from ...core.pipeline import NodeDef


class SeedanceAdapter(Adapter):
    """Seedance 2.0 多供应商后端(负载均衡 + 自动切换)"""

    name = "seedance"

    def __init__(self) -> None:
        # provider 实例缓存:provider_name → SeedanceProvider
        self._provider_cache: dict[str, SeedanceProvider] = {}

    # ── 凭证与供应商解析 ──

    def _get_dry_run(self) -> bool:
        """读取 Seedance_Dry_Run 开关。"""
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        return bool(SERVICE_CONFIG.get_config("Seedance_Dry_Run").data)

    def _get_or_create_provider(self, provider_name: str) -> Optional[SeedanceProvider]:
        """获取或创建 provider 实例(带缓存);未注册返回 None。

        凭证(enabled/api_key/base_url)由注册表的 config_resolver 解析,
        外部插件注册的供应商可用自己插件的配置面板供给凭证。

        每次调用都会**重新解析凭证**,并与缓存实例对比:
        - 凭证未变 → 复用缓存实例(避免无谓重建 httpx 客户端);
        - 凭证已变 → 调 ``provider.update_credentials()`` 热更新,
          保证 Web 控制台改完 key/URL 后下一次请求立刻生效,
          不必重启进程。
        """
        reg = get_registration(provider_name)
        if reg is None:
            logger.warning(f"[Seedance] 供应商 {provider_name} 未注册,跳过")
            return None

        creds = reg.credentials()
        if not creds.base_url:
            logger.warning(f"[Seedance] 供应商 {provider_name} 无可用 Base URL,请求将失败。")
        else:
            logger.info(f"[Seedance] 供应商 {provider_name} 使用 URL: {creds.base_url}")

        cached = self._provider_cache.get(provider_name)
        if cached is not None:
            # 凭证可能变了 —— 比对一次,变了就热更新。
            # 先记下"老值"再调 update_credentials(),否则日志里看到的
            # cached.api_key 已经被覆写,无法反映"是否真的变了"。
            old_key = cached.api_key
            old_url = cached.base_url
            cached_dry_run = bool(getattr(cached, "dry_run", False))
            if (
                old_key != creds.api_key
                or old_url != creds.base_url
                or cached_dry_run != self._get_dry_run()
            ):
                cached.update_credentials(
                    api_key=creds.api_key,
                    base_url=creds.base_url,
                    dry_run=self._get_dry_run(),
                )
                logger.info(
                    f"[Seedance] 供应商 {provider_name} 凭证已热更新 "
                    f"(key_changed={old_key != creds.api_key}, "
                    f"url_changed={old_url != creds.base_url})"
                )
            return cached

        provider = get_provider(
            provider_name,
            api_key=creds.api_key,
            base_url=creds.base_url,
            dry_run=self._get_dry_run(),
        )
        if not creds.api_key:
            logger.warning(
                f"[Seedance] 供应商 {provider_name} 实例化时 API Key 为空,请求将被跳过。"
            )
        self._provider_cache[provider_name] = provider
        return provider

    def _provider_serves_node(self, name: str, reg: Any, node: NodeDef) -> bool:
        """指定供应商是否参与该节点的分发(不含凭证检查)

        - 需要 model 字段的供应商:节点映射(backend_models / 跨插件注入 /
          默认供应商兜底)必须解析出非空 vendor model id;
        - 端点即模型的供应商(如 RunningHub):必须在 node.backend_models 中
          挂名(值可为空串),避免"仅网关可用"的模型被错误分发到它头上。
        """
        if reg.accepts_model_field:
            return bool(self._resolve_model_for_provider(name, node))
        return name in (node.backend_models or {})

    def _resolve_candidates(self, node: NodeDef) -> list[str]:
        """解析候选 provider 列表。

        优先使用节点级固定供应商;否则收集所有"已启用且参与该节点"
        的供应商,再按负载均衡策略排序(跨插件供应商共享同一套熔断状态)。
        """
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        # 节点级固定供应商 → 单元素列表
        if node.provider:
            return [node.provider]

        enabled: list[str] = []
        for name, reg in registered_providers().items():
            if not reg.credentials().enabled:
                continue
            if not self._provider_serves_node(name, reg, node):
                continue
            enabled.append(name)

        if not enabled:
            logger.warning("[Seedance] 所有供应商均已禁用或无模型映射,任务将失败")
            return []

        lb_mode = SERVICE_CONFIG.get_config("Seedance_Load_Balance").data or "round_robin"
        return order_candidates(enabled, load_balance_mode=lb_mode)

    def _get_failure_threshold(self) -> int:
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        raw = SERVICE_CONFIG.get_config("Seedance_Failure_Threshold").data or "3"
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 3

    def _resolve_model_for_provider(
        self, provider_name: str, node: NodeDef
    ) -> Optional[str]:
        """解析指定 provider 应使用的 vendor model ID。

        优先级:
        1. ``node.backend_models[provider_name]``(节点显式多供应商映射)
        2. ``register_vendor_models()`` 注入的跨插件映射
        3. ``node.backend_model``(仅默认供应商 ark 兜底)

        注意:**不接受** ``request.model`` 作为 fallback —— 它是用户指定的
        Pipeline 名(如 ``"seedance2"``),不是 vendor model id;若误塞进
        请求体,会被上游直接以 "model 不存在" 拒掉。

        不接受 model 字段的供应商(如 RunningHub,端点即模型)一律返回 None。
        """
        reg = get_registration(provider_name)
        if reg is not None and not reg.accepts_model_field:
            return None

        node_model = (node.backend_models or {}).get(provider_name) or None
        if not node_model:
            node_model = vendor_model_for(provider_name, node.name)
        if not node_model and provider_name == "ark":
            node_model = node.backend_model
        return node_model

    # ── Adapter 接口 ──

    @staticmethod
    def _provider_configured(reg: Any) -> bool:
        """供应商已启用且凭证齐全(enabled + api_key + base_url)"""
        creds = reg.credentials()
        return bool(creds.enabled and creds.api_key and creds.base_url)

    async def check_available(self) -> bool:
        """后端级可用性:至少一家供应商已启用且凭证齐全"""
        return any(self._provider_configured(reg) for reg in registered_providers().values())

    async def get_unavailable_reason(self) -> str:
        return (
            "Seedance 无已配置的供应商:请在 Web 控制台配置 "
            "Seedance_apikey_ark/_runninghub(或安装并配置外部供应商插件)"
        )

    async def check_node_available(self, node: NodeDef) -> bool:
        """节点级可用性:候选供应商中至少一家凭证齐全且参与该节点"""
        if node.provider:
            reg = get_registration(node.provider)
            return reg is not None and self._provider_configured(reg)
        for name, reg in registered_providers().items():
            if not self._provider_configured(reg):
                continue
            if not self._provider_serves_node(name, reg, node):
                continue
            return True
        return False

    async def get_node_unavailable_reason(self, node: NodeDef) -> str:
        if node.provider:
            reg = get_registration(node.provider)
            if reg is None:
                return f"供应商 {node.provider} 未注册(对应插件未安装?)"
            return f"供应商 {node.provider} 未配置(需要启用开关 + API Key + Base URL)"
        return await self.get_unavailable_reason()

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["video"],
            supported_params=[
                "prompt",
                "images",
                "video_refs",
                "audio_refs",
                "ordered_content",
                "ratio",
                "resolution",
                "duration",
                "seed",
                "generate_audio",
                "watermark",
                "camera_fixed",
                "return_last_frame",
                "service_tier",
            ],
            output_mime=["video/mp4", "image/png"],
            mode="async_poll",
            priority=80,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress: Optional[Any] = None,
    ) -> NodeOutput:
        candidates = self._resolve_candidates(node)
        if not candidates:
            raise RuntimeError(
                "Seedance 无可用供应商(所有候选均被熔断或配置错误),"
                "请检查 Seedance_Enable_ark/_runninghub(或外部供应商插件的启用开关)是否至少启用了一家"
            )

        spec: VideoGenSpec = classify_video_spec(request)
        spec.params.setdefault("draft", request.params.get("draft"))

        # 构建 per-provider 的 model 映射
        model_map: dict[str, Optional[str]] = {}
        for pname in candidates:
            model_map[pname] = self._resolve_model_for_provider(pname, node)

        last_error: Optional[Exception] = None
        attempted: list[str] = []

        threshold = self._get_failure_threshold()

        for provider_name in candidates:
            provider = self._get_or_create_provider(provider_name)
            model_id = model_map.get(provider_name)

            if provider is None:
                attempted.append(f"{provider_name}(未注册)")
                continue
            if not provider.api_key:
                logger.warning(f"[Seedance] 供应商 {provider_name} 缺少 API Key,跳过")
                attempted.append(f"{provider_name}(无key)")
                continue

            attempted.append(provider_name)

            if on_progress is not None:
                await _safe_emit(on_progress, _evt("submitting", 5, f"提交 Seedance({provider_name}) 任务"))

            try:
                final = await provider.run(
                    spec, model=model_id, on_progress=_make_progress_cb(on_progress, provider_name)
                )
            except UnsupportedProviderShapeError as exc:
                # 形态不支持 → 不可重试,直接抛出不降级
                raise RuntimeError(f"供应商 {provider_name} 不支持该任务: {exc}") from exc
            except DryRunInterrupt:
                # Dry-Run: 继承自 BaseException,不会被 except Exception 误捕获;
                # 直接终止,不重试不熔断不 fallback
                raise
            except SeedanceProviderError as exc:
                last_error = exc
                record_provider_failure(provider_name, failure_threshold=threshold)
                logger.warning(
                    f"[Seedance] 供应商 {provider_name} 失败({exc.code or exc}),"
                    f"连续失败={get_failure_count(provider_name)},"
                    f"{'将尝试下一个供应商' if provider_name != candidates[-1] else '已无更多候选'}"
                )
                continue
            except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
                # 网络/超时错误 → 可重试,尝试下一个。
                # 关键:这一分支里 last_error 是裸 httpx / asyncio / OSError,
                # 没有 user_message 属性。如果不包就 raise last_error,
                # 前端会拿到一行 'ReadTimeout:' 这样的空 message —— 不可用。
                # 这里立即包成 SeedanceProviderError(user_message=...) 让
                # 上层 `raise last_error` 自动有干净文案可用。
                last_error = SeedanceProviderError(
                    f"{provider_name} 网络错误({type(exc).__name__}: {exc})",
                    code="PROVIDER_NETWORK_ERROR",
                    provider=provider_name,
                    user_message=_user_msg_for_network_error(exc),
                )
                # 手动挂 __cause__:Python 的 `raise X from Y` 是 raise 语句专属,
                # `assign X(...) from Y` 不是合法语法。这里只赋值不 raise,
                # 想保留异常链只能手动赋值 __cause__。这样 logger.exception
                # / 后续 traceback 仍能看到原始 httpx.ReadTimeout。
                last_error.__cause__ = exc
                record_provider_failure(provider_name, failure_threshold=threshold)
                logger.warning(
                    f"[Seedance] 供应商 {provider_name} 网络错误({type(exc).__name__}: {exc}),"
                    f"连续失败={get_failure_count(provider_name)},"
                    f"{'将尝试下一个供应商' if provider_name != candidates[-1] else '已无更多候选'}"
                )
                continue

            # 成功 → 记录并返回
            record_provider_success(provider_name)

            if not final.video_url:
                raise RuntimeError(f"{provider_name} 任务成功但未返回 video_url: {final.raw}")

            if on_progress is not None:
                await _safe_emit(on_progress, _evt("downloading", 90, "下载视频"))

            video = await _download(final.video_url)

            outputs: dict[str, Any] = {}
            if final.last_frame_url:
                try:
                    outputs["last_frame"] = await _download(final.last_frame_url, timeout=120)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[Seedance] 下载尾帧失败: {exc}")

            usage: dict[str, Any] = dict(final.usage or {})
            usage["task_id"] = final.id
            usage["provider"] = provider_name
            if model_id:
                usage["model"] = model_id

            if on_progress is not None:
                await _safe_emit(on_progress, _evt("done", 100, "Seedance 完成"))

            return NodeOutput(
                status="ok",
                output_type="video",
                data=video,
                mime_type="video/mp4",
                outputs=outputs,
                usage=usage,
                raw=final.raw,
                metadata={
                    "task_id": final.id,
                    "provider": provider_name,
                    "model": model_id,
                    "shape": spec.shape.value,
                    "attempted_providers": attempted,
                },
            )

        # 所有候选均失败
        # 关键设计:不要在这里再包一层 "Seedance 所有供应商均不可用..." 前缀,
        # 直接把最后一个供应商的原始错误(last_error)上抛。
        # 前端最终展示的是该供应商的 failReason / 内层异常文案;
        # 候选列表 / 失败计数等基础设施信息仅入日志,便于排查但不污染用户视图。
        attempted_str = ", ".join(attempted) if attempted else "(无)"
        if last_error is not None:
            logger.error(
                f"[Seedance] 所有候选供应商失败 (已尝试: {attempted_str}). "
                f"最后错误: {last_error!r}"
            )
            raise last_error
        # 极端兜底:连 last_error 都没有(理论上不应发生)
        raise RuntimeError(
            f"Seedance 所有供应商均不可用(已尝试: {attempted_str}),且无更多错误信息"
        )

# ── helpers ──


def _make_progress_cb(on_progress: Optional[Any], provider_name: Optional[str] = None):
    """构建带 provider 上下文的进度回调。"""
    async def _on_progress(task: NormalizedTask) -> None:
        if on_progress is None:
            return
        if task.status.value == "queued":
            await _safe_emit(on_progress, _evt("queued", 25, f"排队中({provider_name or 'unknown'})"))
        elif task.status.value == "running":
            await _safe_emit(on_progress, _evt("running", 55, f"Seedance 生成中({provider_name or 'unknown'})"))
        if task.status.value == "succeeded" and on_progress is not None:
            await _safe_emit(on_progress, _evt("downloading", 90, "下载视频"))
    return _on_progress


async def _download(
    url: str,
    *,
    timeout: float = 300.0,
    max_retries: int = 3,
    initial_backoff: float = 1.0,
) -> bytes:
    """下载 URL 内容,失败时按指数退避重试。

    CDN 临时链接被限流 / 抖动是常态,单次失败就让一次成功任务整体失败太脆。
    默认 3 次,退避 1s → 2s → 4s,只在网络/5xx 错误上重试,
    4xx 客户端错误(URL 失效等)直接抛出不浪费配额。
    """
    last_exc: Optional[BaseException] = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(max_retries):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
            except httpx.HTTPStatusError as exc:
                # 4xx 是请求本身有问题(URL 过期、鉴权失败),重试无意义,直接抛
                if 400 <= exc.response.status_code < 500:
                    raise
                last_exc = exc
            except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
                last_exc = exc
            # 还有重试机会 → 退避后重试
            if attempt < max_retries - 1:
                backoff = initial_backoff * (2**attempt)
                logger.warning(
                    f"[Seedance] 下载失败({type(last_exc).__name__}: {last_exc}),"
                    f" {attempt + 1}/{max_retries} 次重试,等待 {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
    # 全部重试耗尽
    assert last_exc is not None  # 类型系统安慰;逻辑上必赋值
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


__all__ = ["SeedanceAdapter"]


def _user_msg_for_network_error(exc: BaseException) -> str:
    """裸 httpx/asyncio/OSError → 友好 user_message。

    SeedanceAdapter 在 network-error 分支直接 ``raise last_error`` ,前端若直接
    展示 ``str(httpx.ReadTimeout())`` 只会看到 ``"ReadTimeout:"`` (因为
    ``args == ('')`` ),毫无信息量。该函数集中维护网络异常的友好翻译。
    """
    # 顺序按子类层级由细到粗:具体子类型先匹,父类兜底在后。
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
