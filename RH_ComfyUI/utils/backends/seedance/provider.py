"""Seedance Provider 抽象基类

承载所有供应商差异:
- 端点 + HTTP 方法 + 请求体 schema
- 媒体准备(URL 透传 / base64 / 上传)
- 响应解析(信封 / 裸 JSON / 字段重命名)
- 状态枚举归一化
- 用量归一化(口径统一供上层审计/展示)

子类的实现边界:每个 Provider 必须返回
`(method, url, headers, body)` 与 `NormalizedTask` 两套契约。
"""

from __future__ import annotations

import base64
import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Optional
from dataclasses import field, dataclass

import httpx

from gsuid_core.logger import logger

from .spec import VideoGenSpec, VideoTaskShape
from ...core.types import MediaRef


class NormalizedStatus(str, Enum):
    """供应商无关的任务状态"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATUSES: set[NormalizedStatus] = {
    NormalizedStatus.SUCCEEDED,
    NormalizedStatus.FAILED,
    NormalizedStatus.CANCELLED,
    NormalizedStatus.EXPIRED,
}


@dataclass
class NormalizedTask:
    """供应商无关的任务结果"""

    id: str
    status: NormalizedStatus
    video_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    progress: Optional[int] = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


class SeedanceProviderError(RuntimeError):
    """Seedance 供应商错误的统一基类

    Attributes:
        code:        业务错误码(如 ``TASK_FAILED`` / ``VID-INPUT_INVALID``)。
        retryable:   是否可重试(供应商标记,非用户层语义)。
        http_status: HTTP 状态码(如适用)。
        provider:    供应商名(``gateway`` / ``ark`` / ``runninghub``)。
        user_message: 面向最终用户的错误文案。当我们能稳定映射到一条
                      *干净的* 供应商原文(如 ``failReason``)时设置;上游
                      ``canvas_backend.generate`` 会优先用其写库 / 广播,
                      避免前端看到 ``RuntimeError: ...`` 这类堆栈噪音。
    """

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        retryable: bool = False,
        http_status: Optional[int] = None,
        provider: Optional[str] = None,
        user_message: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status
        self.provider = provider
        # 没显式给 user_message 时,默认用 message 本身(保持向后兼容)。
        self.user_message = user_message if user_message is not None else str(message)


class UnsupportedProviderShapeError(SeedanceProviderError):
    """形态/参数不被该供应商支持(能力矩阵拦截)"""


class DryRunInterrupt(BaseException):
    """Dry-Run 模式中断信号。

    继承自 `BaseException` (而非 `Exception`),确保不会被上游的
    ``except Exception`` 误捕获,仅在 ``except BaseException``
    或更顶层的异常处理器中才能捕获它,安全终止流程。

    触发场景:
    - ``SeedanceProvider._request`` 在 ``dry_run=True`` 时拦截出站请求,
      打印完整四元组后抛出。
    - ``SeedanceProviderChannel.invoke`` 捕获后透传,不触发熔断 / 通道切换。
    """


# ── 用量归一化 ──

_VENDOR_UNIT: dict[str, str] = {
    "ark": "tokens",
    "gateway": "tokens",
    "runninghub": "coins",
}


def _pick_str(d: dict[str, Any], key: str) -> Optional[str]:
    v = d.get(key)
    if v is None:
        return None
    return str(v)


def _pick_int(d: dict[str, Any], key: str) -> Optional[int]:
    v = d.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def http_status_retryable(status: int) -> bool:
    """HTTP 状态码 → 换通道是否可能解决。

    5xx / 429(限流)/ 408(超时)是上游侧瞬时问题;401/403 换一家供应商
    (不同 key)可能就通;其余 4xx 属参数类错误,换通道也解决不了。
    """
    if status >= 500:
        return True
    return status in (401, 403, 408, 429)


def normalize_usage(vendor: str, raw: dict[str, Any]) -> dict[str, Any]:
    """把各家异构的 usage 字段归一化为统一二级 Key。"""
    raw = raw or {}
    unit = _VENDOR_UNIT.get(vendor, "")
    cost: Optional[int] = None
    cost_ms: Optional[int] = None

    if vendor == "ark":
        cost = _pick_int(raw, "completion_tokens")
        if cost is None:
            cost = _pick_int(raw, "total_tokens")
    elif vendor == "gateway":
        cost = _pick_int(raw, "totalTokens")
        cost_ms = _pick_int(raw, "durationMs")
    elif vendor == "runninghub":
        cost_str = _pick_str(raw, "consumeCoins")
        if cost_str is not None:
            try:
                cost = int(float(cost_str))
            except (TypeError, ValueError):
                cost = None
        t_str = _pick_str(raw, "taskCostTime")
        if t_str is not None:
            try:
                cost_ms = int(float(t_str) * 1000)
            except (TypeError, ValueError):
                cost_ms = None

    return {
        "vendor": vendor,
        "vendor_unit": unit,
        "vendor_cost": cost,
        "cost_time_ms": cost_ms,
        "raw_usage": raw,
    }


# ═════════════════════════════════════════════════════════════════════
#  SeedanceProvider
# ═════════════════════════════════════════════════════════════════════


class SeedanceProvider(ABC):
    """Seedance 供应商抽象基类

    每个子类需要实现:`render_create`、`parse_create`、`get`,
    以及声明 `STATUS_MAP` / `supported_shapes` / `DEFAULT_BASE_URL` / `name`。
    """

    name: str = ""
    DEFAULT_BASE_URL: str = ""

    # 该家全部状态词(小写)→ 归一化状态
    STATUS_MAP: dict[str, NormalizedStatus] = {}

    # ── 能力矩阵(空集 = 不校验) ──
    supported_shapes: set[VideoTaskShape] = set()
    supported_resolutions: set[str] = set()
    supported_ratios: set[str] = set()

    max_images: int = 9
    max_videos: int = 3
    max_audios: int = 3
    media_prep_concurrency: int = 5

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        # Dry-Run 开关(由 SeedanceProviderChannel 在构造/热更新时注入): 开启后拦截出站请求并打印内容
        self.dry_run = dry_run
        self._client: Optional[httpx.AsyncClient] = None

    # ── 能力校验 ──

    def validate_spec(self, spec: VideoGenSpec) -> None:
        """classify 之后、render 之前调用;不支持的形态/参数直接抛错。"""
        if self.supported_shapes and spec.shape not in self.supported_shapes:
            raise UnsupportedProviderShapeError(
                f"供应商 {self.name or self.__class__.__name__} 不支持任务形态 {spec.shape.value}",
                code="UNSUPPORTED_SHAPE",
            )
        if (
            self.supported_resolutions
            and spec.resolution is not None
            and spec.resolution not in self.supported_resolutions
        ):
            raise UnsupportedProviderShapeError(
                f"供应商 {self.name or self.__class__.__name__} 不支持分辨率 {spec.resolution}",
                code="UNSUPPORTED_RESOLUTION",
            )
        n_img = len(spec.images())
        n_vid = len(spec.videos())
        n_aud = len(spec.audios())
        if n_img > self.max_images or n_vid > self.max_videos or n_aud > self.max_audios:
            raise UnsupportedProviderShapeError(
                f"供应商 {self.name} 媒体数量超限(图≤{self.max_images}/视频≤{self.max_videos}/音频≤{self.max_audios})",
                code="MEDIA_OVERFLOW",
            )

    # ── 媒体准备 ──

    async def materialize_media(self, ref: MediaRef) -> Optional[str]:
        """默认实现:URL 透传 / bytes → base64 data URL。"""
        if ref.url:
            return ref.url
        if ref.data is None:
            return None
        mime = ref.mime_type or "application/octet-stream"
        return self.bytes_to_data_url(ref.data, mime)

    async def materialize_all(self, refs: list[MediaRef]) -> list[Optional[str]]:
        """并行准备多个媒体,结果顺序与输入一致(受 Semaphore 限流)。"""
        if not refs:
            return []
        sem = asyncio.Semaphore(max(1, self.media_prep_concurrency))

        async def _one(r: MediaRef) -> Optional[str]:
            async with sem:
                return await self.materialize_media(r)

        return await asyncio.gather(*(_one(r) for r in refs))

    # ── 子类必须实现 ──

    @abstractmethod
    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        """把 `VideoGenSpec` 渲染为具体的 HTTP 请求四元组。

        Returns:
            (http_method, url, headers, json_body)
        """

    @abstractmethod
    def parse_create(self, resp_json: dict[str, Any]) -> str:
        """从创建响应里抽取任务 ID。"""

    @abstractmethod
    async def get(self, task_id: str) -> NormalizedTask:
        """查询任务状态(各家方法/路径自由),并归一化为 `NormalizedTask`。"""

    # ── 可选钩子 ──

    async def delete(self, task_id: str) -> None:  # noqa: D401
        """取消/删除任务;默认 no-op。"""
        return None

    def transform_prompt(self, prompt: str) -> str:
        """子类可覆盖:提示词引用语法适配(如「图片1」→「@Image 1」)。"""
        return prompt

    # ── 通用基础设施 ──

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def map_status(self, raw_status: str) -> NormalizedStatus:
        return self.STATUS_MAP.get((raw_status or "").lower(), NormalizedStatus.RUNNING)

    def update_credentials(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        *,
        dry_run: Optional[bool] = None,
    ) -> None:
        self.api_key = api_key
        if base_url:
            self.base_url = base_url.rstrip("/")
        if dry_run is not None:
            self.dry_run = dry_run
        self._client = None

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        return self._client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        # ── Dry-Run 拦截: 不真正发送请求,同时 INFO 级打印完整四元组 ──
        if self.dry_run:
            from ._debug import dump_body, mask_body, mask_headers

            logger.info(
                f"[Seedance:{self.name}] Dry-Run 拦截(未发送) {method} {url}\n"
                f"  headers: {mask_headers(headers)}\n"
                f"  body:\n{dump_body(mask_body(json))}"
            )
            raise DryRunInterrupt(
                f"[Seedance:{self.name}] Dry-Run 已启用,请求未发送: {method} {url}"
            )

        # ── 请求日志(脱敏) ──
        from ._debug import dump_body, mask_body, mask_headers

        logger.info(
            f"[Seedance:{self.name}] 请求 {method} {url}\n"
            f"  headers: {mask_headers(headers)}\n"
            f"  body:\n{dump_body(mask_body(json))}"
        )

        # ── 实际发送 ──
        client = self._get_client()
        resp = await client.request(method, url, headers=headers, json=json)

        # ── 响应日志 ──
        try:
            resp_json = resp.json()
            # 响应同样要脱敏:供应商可能把产物直接以 base64 回带(而不是给 URL),
            # 那样一条响应日志就是几 MB,还会顶掉 logger 4096 字符窗口里的其它字段。
            resp_text = dump_body(mask_body(resp_json))
        except Exception:
            resp_text = resp.text[:2000]
        logger.info(f"[Seedance:{self.name}] 响应 {resp.status_code} ({len(resp.content)} bytes)\n  body: {resp_text}")

        if resp.status_code >= 400:
            raise self._build_http_error(resp)
        try:
            return resp.json()
        except Exception as exc:
            raise SeedanceProviderError(
                f"{self.name} 返回非 JSON: {exc}; raw={resp.text[:500]}",
                code="BAD_RESPONSE",
                retryable=True,  # 上游偶发脏响应,换通道可解
                provider=self.name,
                http_status=resp.status_code,
                user_message="上游返回格式异常,请稍后重试。",
            ) from exc

    def _build_http_error(self, resp: httpx.Response) -> SeedanceProviderError:
        # 尝试从响应体抽出干净的供应商 message,透传给前端。
        try:
            err_body: Any = resp.json()
        except Exception:
            err_body = {"raw": resp.text}
        vendor_msg: Optional[str] = None
        if isinstance(err_body, dict):
            err_field = err_body.get("error")
            if isinstance(err_field, dict):
                vendor_msg = err_field.get("message") or err_field.get("code")
            if not vendor_msg:
                vendor_msg = err_body.get("message") or err_body.get("msg")
        return SeedanceProviderError(
            f"{self.name} API 错误 {resp.status_code}: {err_body}",
            code="HTTP_ERROR",
            # 按状态码标注可重试:5xx/429/408 是上游侧问题、401/403 换一家(不同 key)
            # 可能就通;其余 4xx 是参数类错误,换通道也解决不了
            retryable=http_status_retryable(resp.status_code),
            provider=self.name,
            http_status=resp.status_code,
            user_message=str(vendor_msg) if vendor_msg else "上游服务异常,请稍后重试。",
        )

    # ── 共享执行主干 ──

    async def run(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str] = None,
        on_progress: Optional[Callable[[NormalizedTask], Any]] = None,
    ) -> NormalizedTask:
        """校验 → 创建 → 轮询,作为 Adapter 调用的统一入口。"""
        self.validate_spec(spec)
        method, url, headers, body = await self.render_create(spec, model=model)
        logger.info(f"[Seedance:{self.name}] 创建任务: model={model}, endpoint={url}, keys={list(body.keys())}")
        resp = await self._request(method, url, headers=headers, json=body)
        task_id = self.parse_create(resp)
        if not task_id:
            raise SeedanceProviderError(
                f"{self.name} 未返回 task id: {resp}",
                code="NO_TASK_ID",
                retryable=True,  # 尚未产生任务,换通道重建无重复计费风险
                provider=self.name,
                user_message="上游未返回任务 ID,请稍后重试。",
            )
        logger.info(f"[Seedance:{self.name}] 任务已创建: task_id={task_id}")
        return await self.poll_until_done(task_id, on_progress=on_progress)

    async def poll_until_done(
        self,
        task_id: str,
        *,
        interval: float = 8.0,
        max_wait: float = 1800.0,
        heartbeat_every: int = 5,
        on_progress: Optional[Callable[[NormalizedTask], Any]] = None,
    ) -> NormalizedTask:
        """基于 `NormalizedTask.status` 轮询,直至进入终态。

        心跳日志: 每 ``heartbeat_every`` 次轮询打一行 INFO,避免长任务时
        日志完全静默导致误判卡死。状态变化或进度变化时立即补打一行。
        """

        loop = asyncio.get_event_loop()
        start = loop.time()
        last_status: Optional[NormalizedStatus] = None
        last_progress: Any = None
        poll_count = 0
        while True:
            poll_count += 1
            try:
                task = await self.get(task_id)
            except (httpx.HTTPError, asyncio.TimeoutError, OSError) as poll_exc:
                # 网络级错误: 附加上次状态 / 已等待时长 / 轮询次数 后包装抛出,
                # 让上层(executor)能记录到上下文而非裸 ReadTimeout。
                elapsed = loop.time() - start
                raise SeedanceProviderError(
                    f"{self.name} 轮询失败({type(poll_exc).__name__}: {poll_exc}); "
                    f"上次状态={last_status}, 进度={last_progress}, "
                    f"已等待={elapsed:.1f}s, 轮询次数={poll_count}",
                    code="POLL_NETWORK_ERROR",
                    # 任务可能已在上游成功,换通道会整单重跑(重复烧钱)——
                    # 与 RESULT_DOWNLOAD_FAILED 同理,不做通道切换
                    retryable=False,
                    provider=self.name,
                    user_message="轮询阶段网络异常,请稍后重试。",
                ) from poll_exc

            # 状态变更 → 立即打日志,并重置心跳计数
            if task.status != last_status:
                logger.info(
                    f"[Seedance:{self.name}] 任务 {task_id} 状态变更: "
                    f"{last_status} → {task.status}, 进度={task.progress}"
                )
                last_status = task.status
                last_progress = task.progress
                poll_count = 0
            elif task.progress != last_progress:
                # 状态没变但进度有变 → 顺手更新 last_progress,避免心跳日志刷陈旧值
                last_progress = task.progress

            # 长轮询心跳(状态变化已重置过 poll_count,这里只在无变化时累计到阈值)
            if poll_count > 0 and poll_count % heartbeat_every == 0:
                elapsed = loop.time() - start
                logger.info(
                    f"[Seedance:{self.name}] 任务 {task_id} 轮询中: "
                    f"status={task.status}, 进度={task.progress}, "
                    f"已等待={elapsed:.1f}s, 轮询次数={poll_count}"
                )

            if on_progress is not None:
                try:
                    res = on_progress(task)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[Seedance:{self.name}] 进度回调异常: {exc}")

            if task.status in TERMINAL_STATUSES:
                if task.status == NormalizedStatus.FAILED:
                    # 任务失败:优先把供应商原始 failReason 透传到上层,
                    # 不要再包一层 "gateway 任务失败: ..." 的前缀 ——
                    # 前端最终展示的就是这条原始文案(Request id 等供应商
                    # 自带的排查信息一并保留)。
                    vendor_msg = task.error or str(task.raw)
                    raise SeedanceProviderError(
                        vendor_msg,
                        code="TASK_FAILED",
                        retryable=True,  # 该家跑挂了(容量/内部错误),换一家值得一试
                        provider=self.name,
                        user_message=vendor_msg,
                    )
                if task.status == NormalizedStatus.EXPIRED:
                    raise SeedanceProviderError(
                        f"任务已过期:{task_id}",
                        code="TASK_EXPIRED",
                        retryable=True,
                        provider=self.name,
                        user_message="生成任务已过期,请重试。",
                    )
                return task

            elapsed = loop.time() - start
            if elapsed > max_wait:
                raise asyncio.TimeoutError(
                    f"{self.name} 任务等待超时 ({max_wait}s): {task_id} (最后状态={last_status}, 进度={last_progress})"
                )

            await asyncio.sleep(interval)

    @staticmethod
    def bytes_to_data_url(data: bytes, mime: str = "application/octet-stream") -> str:
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{encoded}"


__all__ = [
    "NormalizedStatus",
    "TERMINAL_STATUSES",
    "NormalizedTask",
    "SeedanceProviderError",
    "UnsupportedProviderShapeError",
    "DryRunInterrupt",
    "SeedanceProvider",
    "normalize_usage",
    "http_status_retryable",
]
