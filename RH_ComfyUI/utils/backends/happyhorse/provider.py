"""DashScope HappyHorse Provider

端点:
- 创建: POST {base}/services/aigc/video-generation/video-synthesis
  头: X-DashScope-Async: enable, Authorization: Bearer <key>
- 查询: GET {base}/tasks/{task_id}

请求体统一结构:
  {model, input: {prompt?, media?}, parameters: {resolution, duration, ratio?, ...}}
"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Callable, Optional

import httpx

from gsuid_core.logger import logger

from .classify import (
    to_api_resolution,
    rewrite_prompt_for_r2v,
)
from ...core.types import MediaRef, MediaKind
from ..seedance.spec import MediaRole, VideoGenSpec, VideoTaskShape
from ...core.safe_json import dump_body, mask_body

# 复用 seedance 的 header 脱敏
from ..seedance._debug import mask_headers
from ..seedance.provider import (
    TERMINAL_STATUSES,
    NormalizedTask,
    DryRunInterrupt,
    NormalizedStatus,
    SeedanceProviderError,
    http_status_retryable,
)


def _field_text(value: Any) -> Optional[str]:
    """空 / null / None 视为未解到,其余原样转成字符串。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text


def _pick_vendor_message(
    resp_json: dict[str, Any],
    *,
    http_status: Optional[int] = None,
) -> str:
    """错误文案优先级: message → msg → code → HTTP status。"""
    data = resp_json.get("data") if isinstance(resp_json.get("data"), dict) else {}
    err = resp_json.get("error") if isinstance(resp_json.get("error"), dict) else {}
    for value in (resp_json.get("message"), data.get("message"), err.get("message")):
        text = _field_text(value)
        if text is not None:
            return text
    for value in (resp_json.get("msg"), data.get("msg")):
        text = _field_text(value)
        if text is not None:
            return text
    for value in (resp_json.get("code"), data.get("code")):
        text = _field_text(value)
        if text is not None:
            return text
    if http_status is not None:
        return str(http_status)
    return "上游错误"


class HappyHorseProviderError(SeedanceProviderError):
    """HappyHorse 供应商错误(复用 Seedance 错误字段语义)。"""


class HappyHorseProvider:
    """DashScope HappyHorse 单一供应商实现。"""

    name = "dashscope"
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

    CREATE_PATH = "/services/aigc/video-generation/video-synthesis"
    TASK_PATH = "/tasks/{task_id}"
    # DashScope 异步任务取消: POST /api/v1/tasks/{task_id}/cancel
    # 仅 PENDING(排队)可取消;已 RUNNING 会 400 UnsupportedOperation
    CANCEL_PATH = "/tasks/{task_id}/cancel"

    STATUS_MAP: dict[str, NormalizedStatus] = {
        "pending": NormalizedStatus.QUEUED,
        "running": NormalizedStatus.RUNNING,
        "succeeded": NormalizedStatus.SUCCEEDED,
        "failed": NormalizedStatus.FAILED,
        "canceled": NormalizedStatus.CANCELLED,
        "cancelled": NormalizedStatus.CANCELLED,
        "unknown": NormalizedStatus.EXPIRED,
    }

    supported_shapes = {
        VideoTaskShape.TEXT2VIDEO,
        VideoTaskShape.IMAGE2VIDEO,
        VideoTaskShape.MULTIMODAL,
        VideoTaskShape.FIRST_LAST_FRAME,
        VideoTaskShape.VIDEO_EDIT,
    }
    supported_resolutions = {"480p", "720p", "1080p"}
    supported_ratios = {
        "16:9",
        "9:16",
        "1:1",
        "4:3",
        "3:4",
        "4:5",
        "5:4",
        "9:21",
        "21:9",
    }
    min_duration = 3
    max_duration = 15
    max_images = 9
    max_videos = 1
    max_audios = 0
    media_prep_concurrency = 5
    poll_interval = 15.0  # 官方建议 15s

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.dry_run = dry_run
        self._client: Optional[httpx.AsyncClient] = None

    # ── 凭证 ──

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

    # ── 能力 ──

    def can_handle_spec(self, spec: VideoGenSpec) -> bool:
        if self.supported_shapes and spec.shape not in self.supported_shapes:
            return False
        res = (spec.resolution or "").lower()
        if self.supported_resolutions and res and res not in self.supported_resolutions:
            return False
        if (
            self.supported_ratios
            and spec.ratio is not None
            and spec.ratio not in self.supported_ratios
            and spec.shape != VideoTaskShape.IMAGE2VIDEO  # i2v 宽高比跟随首帧
        ):
            return False
        if self.min_duration and spec.duration and spec.duration < self.min_duration:
            return False
        if self.max_duration and spec.duration and spec.duration > self.max_duration:
            return False
        return True

    def validate_spec(self, spec: VideoGenSpec) -> None:
        if self.supported_shapes and spec.shape not in self.supported_shapes:
            raise HappyHorseProviderError(
                f"HappyHorse 不支持任务形态 {spec.shape.value}",
                code="UNSUPPORTED_SHAPE",
                provider=self.name,
            )
        n_img = len(spec.images())
        n_vid = len(spec.videos())
        n_aud = len(spec.audios())
        if n_aud > 0:
            raise HappyHorseProviderError(
                "HappyHorse 不支持参考音频",
                code="UNSUPPORTED_AUDIO",
                provider=self.name,
                user_message="HappyHorse 不支持参考音频,请移除音频素材。",
            )
        if n_vid > self.max_videos:
            raise HappyHorseProviderError(
                f"HappyHorse 最多 1 段输入视频,当前 {n_vid}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if n_img > self.max_images:
            raise HappyHorseProviderError(
                f"HappyHorse 最多 {self.max_images} 张参考图,当前 {n_img}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if spec.shape == VideoTaskShape.TEXT2VIDEO:
            if not (spec.prompt or "").strip():
                raise HappyHorseProviderError(
                    "文生视频必须提供提示词",
                    code="MISSING_PROMPT",
                    provider=self.name,
                    user_message="文生视频需要填写提示词。",
                )
        elif spec.shape == VideoTaskShape.IMAGE2VIDEO:
            if n_img < 1:
                raise HappyHorseProviderError(
                    "图生视频需要至少 1 张首帧图",
                    code="MISSING_IMAGE",
                    provider=self.name,
                    user_message="图生视频需要上传 1 张首帧图片。",
                )
        elif spec.shape in (VideoTaskShape.MULTIMODAL, VideoTaskShape.FIRST_LAST_FRAME):
            if n_img < 1 and n_vid < 1:
                raise HappyHorseProviderError(
                    "参考生视频需要至少 1 张参考图或 1 段参考视频",
                    code="MISSING_IMAGE",
                    provider=self.name,
                    user_message="参考生视频需要至少 1 张参考图或 1 段参考视频。",
                )
            if not (spec.prompt or "").strip():
                raise HappyHorseProviderError(
                    "参考生视频必须提供提示词",
                    code="MISSING_PROMPT",
                    provider=self.name,
                    user_message="参考生视频需要填写提示词(可用 [Image 1] 引用图片)。",
                )
        elif spec.shape == VideoTaskShape.VIDEO_EDIT:
            if n_vid < 1:
                raise HappyHorseProviderError(
                    "视频编辑需要 1 段输入视频",
                    code="MISSING_VIDEO",
                    provider=self.name,
                    user_message="视频编辑需要上传 1 段待编辑视频。",
                )
            if n_img > 5:
                raise HappyHorseProviderError(
                    f"视频编辑最多 5 张参考图,当前 {n_img}",
                    code="MEDIA_OVERFLOW",
                    provider=self.name,
                )
            if not (spec.prompt or "").strip():
                raise HappyHorseProviderError(
                    "视频编辑必须提供提示词",
                    code="MISSING_PROMPT",
                    provider=self.name,
                    user_message="视频编辑需要填写编辑意图提示词。",
                )
            # video-edit 仅 720P/1080P
            res = (spec.resolution or "1080p").lower()
            if res not in ("720p", "1080p"):
                raise HappyHorseProviderError(
                    f"视频编辑不支持分辨率 {spec.resolution},可选 720p / 1080p",
                    code="UNSUPPORTED_RESOLUTION",
                    provider=self.name,
                )

    # ── 媒体 ──

    async def materialize_media(self, ref: MediaRef) -> Optional[str]:
        if ref.url:
            return ref.url
        if ref.data is None:
            return None
        mime = ref.mime_type or "application/octet-stream"
        b64 = base64.b64encode(ref.data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    async def materialize_all(self, refs: list[MediaRef]) -> list[Optional[str]]:
        if not refs:
            return []
        sem = asyncio.Semaphore(max(1, self.media_prep_concurrency))

        async def _one(r: MediaRef) -> Optional[str]:
            async with sem:
                return await self.materialize_media(r)

        return await asyncio.gather(*(_one(r) for r in refs))

    # ── 渲染 ──

    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        if not model:
            raise HappyHorseProviderError(
                "缺少供应商 model ID",
                code="MISSING_MODEL",
                provider=self.name,
            )

        media_items = await self._build_media(spec)
        prompt = (spec.prompt or "").strip()
        if spec.shape in (VideoTaskShape.MULTIMODAL, VideoTaskShape.FIRST_LAST_FRAME):
            prompt = rewrite_prompt_for_r2v(prompt)

        input_body: dict[str, Any] = {}
        if prompt:
            input_body["prompt"] = prompt
        if media_items:
            input_body["media"] = media_items

        parameters: dict[str, Any] = {}
        api_res = to_api_resolution(spec.resolution)
        if api_res:
            parameters["resolution"] = api_res

        if spec.shape != VideoTaskShape.VIDEO_EDIT:
            if spec.duration:
                parameters["duration"] = int(spec.duration)
            # i2v 宽高比跟随首帧,不传 ratio
            if spec.shape != VideoTaskShape.IMAGE2VIDEO and spec.ratio:
                parameters["ratio"] = spec.ratio
        else:
            # video-edit: audio_setting
            audio_setting = spec.params.get("audio_setting") or "auto"
            if audio_setting in ("auto", "origin"):
                parameters["audio_setting"] = audio_setting

        # 默认关水印(与 seedance schema 对齐);API 默认 true
        parameters["watermark"] = bool(spec.watermark)
        if spec.seed is not None:
            parameters["seed"] = int(spec.seed)

        body: dict[str, Any] = {
            "model": model,
            "input": input_body,
            "parameters": parameters,
        }
        url = f"{self.base_url}{self.CREATE_PATH}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        return "POST", url, headers, body

    async def _build_media(self, spec: VideoGenSpec) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if spec.shape == VideoTaskShape.TEXT2VIDEO:
            return items

        if spec.shape == VideoTaskShape.IMAGE2VIDEO:
            first = next(
                (m for m in spec.images() if m.role == MediaRole.FIRST_FRAME),
                None,
            ) or (spec.images()[0] if spec.images() else None)
            if first is None:
                return items
            url = await self.materialize_media(first.ref)
            if url:
                items.append({"type": "first_frame", "url": url})
            return items

        if spec.shape in (VideoTaskShape.MULTIMODAL, VideoTaskShape.FIRST_LAST_FRAME):
            # 按 spec.media 原序:图=reference_image,视频=video(r2v 参考,非编辑)
            refs = list(spec.media) if spec.media else list(spec.images()) + list(spec.videos())
            urls = await self.materialize_all([m.ref for m in refs])
            for media, url in zip(refs, urls, strict=False):
                if not url:
                    continue
                if media.kind == MediaKind.VIDEO:
                    items.append({"type": "video", "url": url})
                else:
                    items.append({"type": "reference_image", "url": url})
            return items

        if spec.shape == VideoTaskShape.VIDEO_EDIT:
            # 必须先 video,再 reference_image
            for v in spec.videos()[:1]:
                url = await self.materialize_media(v.ref)
                if url:
                    items.append({"type": "video", "url": url})
            urls = await self.materialize_all([m.ref for m in spec.images()[:5]])
            for url in urls:
                if url:
                    items.append({"type": "reference_image", "url": url})
            return items

        return items

    def parse_create(self, resp_json: dict[str, Any]) -> str:
        output = resp_json.get("output") or {}
        task_id = output.get("task_id") or resp_json.get("task_id") or ""
        return str(task_id) if task_id else ""

    async def get(self, task_id: str) -> NormalizedTask:
        url = f"{self.base_url}{self.TASK_PATH.format(task_id=task_id)}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = await self._request("GET", url, headers=headers)
        return self._parse_task(resp)

    def _parse_task(self, resp_json: dict[str, Any]) -> NormalizedTask:
        output = resp_json.get("output") or {}
        usage_raw = resp_json.get("usage") or {}
        raw_status = str(output.get("task_status") or "").lower()
        status = self.STATUS_MAP.get(raw_status, NormalizedStatus.RUNNING)
        video_url = output.get("video_url")
        error = None
        if status == NormalizedStatus.FAILED:
            code = output.get("code") or ""
            msg = output.get("message") or "任务失败"
            error = f"{code}: {msg}" if code else str(msg)

        usage: dict[str, Any] = {
            "vendor": self.name,
            "vendor_unit": "seconds",
            "vendor_cost": usage_raw.get("duration") or usage_raw.get("output_video_duration"),
            "raw_usage": usage_raw,
        }
        return NormalizedTask(
            id=str(output.get("task_id") or ""),
            status=status,
            video_url=str(video_url) if video_url else None,
            usage=usage,
            error=error,
            raw=resp_json if isinstance(resp_json, dict) else {},
        )

    # ── HTTP ──

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
        if self.dry_run:
            logger.info(
                f"[HappyHorse:{self.name}] Dry-Run 拦截(未发送) {method} {url}\n"
                f"  headers: {mask_headers(headers)}\n"
                f"  body:\n{dump_body(mask_body(json))}"
            )
            raise DryRunInterrupt(f"[HappyHorse:{self.name}] Dry-Run 已启用,请求未发送: {method} {url}")

        logger.debug(
            f"[HappyHorse:{self.name}] 请求 {method} {url}\n"
            f"  headers: {mask_headers(headers)}\n"
            f"  body:\n{dump_body(mask_body(json))}"
        )
        client = self._get_client()
        resp = await client.request(method, url, headers=headers, json=json)
        resp_json: Any = None
        try:
            resp_json = resp.json()
            resp_text = dump_body(mask_body(resp_json))
        except Exception:
            resp_text = resp.text[:2000]
        emit = logger.warning if resp.status_code >= 400 else logger.debug
        emit(f"[HappyHorse:{self.name}] 响应 {resp.status_code} ({len(resp.content)} bytes)\n  body: {resp_text}")
        if resp.status_code >= 400:
            raise self._build_http_error(resp)
        # DELETE/空 body 响应
        if method.upper() == "DELETE" and (resp_json is None or not (resp.content or b"").strip()):
            return {}
        # DashScope 业务错误有时 200 但带 code/message
        # 取消成功通常只有 request_id,无 output.task_id;失败才有顶层 code
        if isinstance(resp_json, dict):
            self._raise_if_dashscope_business_error(resp_json)
            return resp_json
        if method.upper() in ("DELETE", "POST") and not (resp.content or b"").strip():
            return {}
        raise HappyHorseProviderError(
            f"{self.name} 返回非 JSON: {resp_text[:500]}",
            code="BAD_RESPONSE",
            retryable=True,
            provider=self.name,
            user_message="上游返回格式异常,请稍后重试。",
        )

    def _raise_if_dashscope_business_error(self, resp_json: dict[str, Any]) -> None:
        """HTTP 200 + 顶层 code 且无 output.task_id → DashScope 业务错。

        网关子类覆盖为空操作:其 ``{code,msg,data}`` 信封留给 parse_create/get
        的 ``_unwrap``(否则 ``msg`` 丢失,只剩数字 ``500``)。
        """
        if not resp_json.get("code"):
            return
        if (resp_json.get("output") or {}).get("task_id"):
            return
        code = str(resp_json.get("code") or "")
        if not code or code in ("", "Success", "null"):
            return
        msg = _pick_vendor_message(resp_json)
        raise HappyHorseProviderError(
            f"DashScope 错误 {code}: {msg}",
            code=code,
            retryable=False,
            provider=self.name,
            user_message=str(msg),
        )

    def _build_http_error(self, resp: httpx.Response) -> HappyHorseProviderError:
        try:
            err_body: Any = resp.json()
        except Exception:
            err_body = {"raw": resp.text}
        vendor_msg = (
            _pick_vendor_message(err_body, http_status=resp.status_code)
            if isinstance(err_body, dict)
            else str(resp.status_code)
        )
        return HappyHorseProviderError(
            f"{self.name} API 错误 {resp.status_code}: {err_body}",
            code="HTTP_ERROR",
            retryable=http_status_retryable(resp.status_code),
            provider=self.name,
            http_status=resp.status_code,
            user_message=vendor_msg,
        )

    # ── 执行主干 ──

    async def run(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str] = None,
        on_progress: Optional[Callable[[NormalizedTask], Any]] = None,
    ) -> NormalizedTask:
        self.validate_spec(spec)
        method, url, headers, body = await self.render_create(spec, model=model)
        masked = mask_body(body)
        logger.info(
            f"[HappyHorse:{self.name}] 创建任务: model={model}, endpoint={url}\n  request:\n{dump_body(masked)}"
        )
        from ....core.telemetry.wire_capture import set_wire_from_http_body

        set_wire_from_http_body(masked)
        resp = await self._request(method, url, headers=headers, json=body)
        task_id = self.parse_create(resp)
        if not task_id:
            raise HappyHorseProviderError(
                f"{self.name} 未返回 task id: {resp}",
                code="NO_TASK_ID",
                retryable=True,
                provider=self.name,
                user_message="上游未返回任务 ID,请稍后重试。",
            )
        logger.info(f"[HappyHorse:{self.name}] 任务已创建: task_id={task_id}")
        await self._bind_active_cancel(task_id)
        try:
            return await self.poll_until_done(task_id, on_progress=on_progress)
        except asyncio.CancelledError:
            await self._best_effort_delete(task_id)
            raise

    async def delete(self, task_id: str) -> None:
        """取消异步任务(DashScope 官方接口)。

        ``POST {base}/tasks/{task_id}/cancel``
        (完整 URL 例: ``https://dashscope.aliyuncs.com/api/v1/tasks/{id}/cancel``)

        文档约定:
        - **仅 PENDING(排队)可取消**;已 RUNNING 返回 400 UnsupportedOperation
        - 成功响应通常只有 ``request_id``;失败带 ``code`` / ``message``
        - 本地 cancel 仍会退引擎预扣;RUNNING 时上游可能继续计费,宿主 UI 应提示
        """
        if not task_id:
            return
        path = self.CANCEL_PATH if hasattr(self, "CANCEL_PATH") and self.CANCEL_PATH else "/tasks/{task_id}/cancel"
        url = f"{self.base_url}{path.format(task_id=task_id)}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        logger.info(f"[HappyHorse:{self.name}] 上游 cancel POST task_id={task_id} url={url}")
        try:
            # POST 无 body;_request 在 dry_run 下抛 DryRunInterrupt
            await self._request("POST", url, headers=headers, json=None)
            logger.info(f"[HappyHorse:{self.name}] 上游 cancel 完成 task_id={task_id}")
        except DryRunInterrupt:
            logger.info(f"[HappyHorse:{self.name}] Dry-Run 跳过 cancel task_id={task_id}")
            return

    def supports_remote_cancel(self) -> bool:
        """DashScope/网关 HappyHorse 均有真实 cancel;不支持的子类须覆盖为 False。"""
        return True

    async def _bind_active_cancel(self, task_id: str) -> None:
        if not task_id:
            return
        try:
            from ....core.dispatch.active_tasks import get_active_task_registry

            provider = self
            cancel_remote = None
            if self.supports_remote_cancel():

                async def _cancel_remote() -> None:
                    await provider.delete(task_id)

                cancel_remote = _cancel_remote

            await get_active_task_registry().bind_vendor_task(
                vendor_task_id=task_id,
                cancel_remote=cancel_remote,
                channel_name=self.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[HappyHorse:{self.name}] 绑定 vendor 任务失败(忽略): {exc}")

    async def _best_effort_delete(self, task_id: str) -> None:
        if not task_id or not self.supports_remote_cancel():
            return
        try:
            from ....core.dispatch.active_tasks import remote_cancel_already_attempted

            if remote_cancel_already_attempted():
                logger.debug(
                    f"[HappyHorse:{self.name}] 跳过 CancelledError 兜底 cancel"
                    f"(cancel_generation 已尝试上游): {task_id}"
                )
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.delete(task_id)
            logger.info(f"[HappyHorse:{self.name}] CancelledError 兜底已 cancel 上游任务 {task_id}")
        except Exception as exc:  # noqa: BLE001
            # RUNNING 态取消会 400,本地 Task 仍会被 cancel,只记 warning
            logger.warning(f"[HappyHorse:{self.name}] CancelledError 兜底 cancel 失败: {exc}")

    async def poll_until_done(
        self,
        task_id: str,
        *,
        interval: Optional[float] = None,
        max_wait: float = 1800.0,
        heartbeat_every: int = 8,
        on_progress: Optional[Callable[[NormalizedTask], Any]] = None,
    ) -> NormalizedTask:
        poll_iv = interval if interval is not None else self.poll_interval
        loop = asyncio.get_event_loop()
        start = loop.time()
        last_status: Optional[NormalizedStatus] = None
        poll_count = 0
        while True:
            poll_count += 1
            try:
                task = await self.get(task_id)
            except (httpx.HTTPError, asyncio.TimeoutError, OSError) as poll_exc:
                elapsed = loop.time() - start
                raise HappyHorseProviderError(
                    f"{self.name} 轮询失败({type(poll_exc).__name__}: {poll_exc}); "
                    f"上次状态={last_status}, 已等待={elapsed:.1f}s",
                    code="POLL_NETWORK_ERROR",
                    retryable=False,
                    provider=self.name,
                    user_message="轮询阶段网络异常,请稍后重试。",
                ) from poll_exc

            if task.status != last_status:
                logger.info(f"[HappyHorse:{self.name}] 任务 {task_id} 状态变更: {last_status} → {task.status}")
                last_status = task.status
                poll_count = 0
            elif poll_count > 0 and poll_count % heartbeat_every == 0:
                elapsed = loop.time() - start
                logger.info(
                    f"[HappyHorse:{self.name}] 任务 {task_id} 轮询中: status={task.status}, 已等待={elapsed:.1f}s"
                )

            if on_progress is not None:
                try:
                    res = on_progress(task)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[HappyHorse:{self.name}] 进度回调异常: {exc}")

            if task.status in TERMINAL_STATUSES:
                raw_dump = dump_body(mask_body(task.raw))
                if task.status == NormalizedStatus.FAILED:
                    vendor_msg = task.error or str(task.raw)
                    logger.warning(f"[HappyHorse:{self.name}] 任务 {task_id} 失败: {vendor_msg}\n  result:\n{raw_dump}")
                    raise HappyHorseProviderError(
                        vendor_msg,
                        code="TASK_FAILED",
                        retryable=True,
                        provider=self.name,
                        user_message=vendor_msg,
                    )
                if task.status in (NormalizedStatus.EXPIRED, NormalizedStatus.CANCELLED):
                    raise HappyHorseProviderError(
                        f"任务 {task.status.value}: {task_id}",
                        code=task.status.value.upper(),
                        retryable=False,
                        provider=self.name,
                        user_message=f"任务已{task.status.value}",
                    )
                logger.info(f"[HappyHorse:{self.name}] 任务 {task_id} 成功\n  result:\n{raw_dump}")
                return task

            if loop.time() - start > max_wait:
                raise HappyHorseProviderError(
                    f"任务轮询超时({max_wait}s): {task_id}",
                    code="POLL_TIMEOUT",
                    retryable=False,
                    provider=self.name,
                    user_message="生成超时,请稍后重试。",
                )
            await asyncio.sleep(poll_iv)


__all__ = [
    "HappyHorseProvider",
    "HappyHorseProviderError",
    "_pick_vendor_message",
]
