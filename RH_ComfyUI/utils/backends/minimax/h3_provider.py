"""MiniMax H3 视频生成 Provider

官方 Video Generation V2:
- 创建: POST {base}/v2/video_generation
- 查询: GET  {base}/v2/query/video_generation/{task_id}
- 取消: DELETE {base}/v2/video_generation/{task_id}
  queued → cancelled;running 不可取消;succeeded/failed → deleted

错误体为 OpenAI 风格 {type,error{type,message,http_code}}。
"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Callable, Optional

import httpx

from gsuid_core.logger import logger

from .config import minimax_api_key, minimax_dry_run
from ..http_retry import RetryingAsyncClient
from .h3_classify import to_api_ratio, to_api_resolution
from ...core.types import MediaRef, MediaKind
from ..seedance.spec import MediaRole, SpecMedia, VideoGenSpec, VideoTaskShape
from ...core.safe_json import dump_body, mask_body
from ..seedance._debug import mask_headers
from ..seedance.provider import (
    TERMINAL_STATUSES,
    NormalizedTask,
    DryRunInterrupt,
    NormalizedStatus,
    SeedanceProviderError,
    http_status_retryable,
)


class MiniMaxH3ProviderError(SeedanceProviderError):
    """MiniMax H3 供应商错误。"""


def _field_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _pick_oai_message(resp_json: dict[str, Any], *, http_status: Optional[int] = None) -> str:
    err = _as_dict(resp_json["error"] if "error" in resp_json else None)
    candidates = (
        err["message"] if "message" in err else None,
        resp_json["message"] if "message" in resp_json else None,
        resp_json["msg"] if "msg" in resp_json else None,
    )
    for value in candidates:
        text = _field_text(value)
        if text is not None:
            return text
    if http_status is not None:
        return str(http_status)
    return "上游错误"


class MiniMaxH3Provider:
    """官方 MiniMax-H3 单一供应商。"""

    name = "minimax-h3"
    DEFAULT_BASE_URL = "https://api.minimaxi.com"
    VENDOR_MODEL = "MiniMax-H3"

    CREATE_PATH = "/v2/video_generation"
    QUERY_PATH = "/v2/query/video_generation/{task_id}"
    DELETE_PATH = "/v2/video_generation/{task_id}"

    STATUS_MAP: dict[str, NormalizedStatus] = {
        "queued": NormalizedStatus.QUEUED,
        "running": NormalizedStatus.RUNNING,
        "succeeded": NormalizedStatus.SUCCEEDED,
        "success": NormalizedStatus.SUCCEEDED,
        "failed": NormalizedStatus.FAILED,
        "cancelled": NormalizedStatus.CANCELLED,
        "canceled": NormalizedStatus.CANCELLED,
    }

    supported_shapes = {
        VideoTaskShape.TEXT2VIDEO,
        VideoTaskShape.IMAGE2VIDEO,
        VideoTaskShape.FIRST_LAST_FRAME,
        VideoTaskShape.MULTIMODAL,
    }
    supported_resolutions = {"768p", "2k"}
    supported_ratios = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
    min_duration = 4
    max_duration = 15
    max_images = 9
    max_videos = 3
    max_audios = 3
    max_reference_total = 12
    max_prompt_chars = 7000
    media_prep_concurrency = 5
    poll_interval = 10.0

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

    def can_handle_spec(self, spec: VideoGenSpec) -> bool:
        if spec.shape not in self.supported_shapes:
            return False
        res = to_api_resolution(spec.resolution).lower()
        if res not in {"768p", "2k"}:
            return False
        if spec.duration and not (self.min_duration <= int(spec.duration) <= self.max_duration):
            return False
        return True

    def validate_spec(self, spec: VideoGenSpec) -> None:
        prompt = (spec.prompt or "").strip()
        if not prompt:
            raise MiniMaxH3ProviderError(
                "MiniMax H3 必须提供非空提示词",
                code="MISSING_PROMPT",
                provider=self.name,
                user_message="请填写视频提示词。",
            )
        if len(prompt) > self.max_prompt_chars:
            raise MiniMaxH3ProviderError(
                f"提示词最长 {self.max_prompt_chars} 字符,当前 {len(prompt)}",
                code="PROMPT_TOO_LONG",
                provider=self.name,
                user_message=f"提示词超过 {self.max_prompt_chars} 字符,请缩短后重试。",
            )
        if spec.shape not in self.supported_shapes:
            raise MiniMaxH3ProviderError(
                f"MiniMax H3 不支持任务形态 {spec.shape.value}",
                code="UNSUPPORTED_SHAPE",
                provider=self.name,
            )
        n_img = len(spec.images())
        n_vid = len(spec.videos())
        n_aud = len(spec.audios())
        if n_img > self.max_images:
            raise MiniMaxH3ProviderError(
                f"MiniMax H3 最多 {self.max_images} 张参考图,当前 {n_img}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if n_vid > self.max_videos:
            raise MiniMaxH3ProviderError(
                f"MiniMax H3 最多 {self.max_videos} 段参考视频,当前 {n_vid}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if n_aud > self.max_audios:
            raise MiniMaxH3ProviderError(
                f"MiniMax H3 最多 {self.max_audios} 段参考音频,当前 {n_aud}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if n_img + n_vid + n_aud > self.max_reference_total:
            raise MiniMaxH3ProviderError(
                f"MiniMax H3 参考素材合计最多 {self.max_reference_total} 个,"
                f"当前 {n_img + n_vid + n_aud}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        has_frame = spec.shape in (VideoTaskShape.IMAGE2VIDEO, VideoTaskShape.FIRST_LAST_FRAME)
        has_ref = spec.shape == VideoTaskShape.MULTIMODAL
        if has_frame and (n_vid or n_aud):
            raise MiniMaxH3ProviderError(
                "首尾帧图生与参考视频/音频不能混用",
                code="SHAPE_CONFLICT",
                provider=self.name,
                user_message="首尾帧模式不能同时传参考视频或音频;请改用参考模式或移除音视频。",
            )
        if has_ref and n_img + n_vid + n_aud < 1:
            raise MiniMaxH3ProviderError(
                "多模态参考至少需要 1 个图片/视频/音频素材",
                code="MISSING_MEDIA",
                provider=self.name,
                user_message="参考生视频需要至少 1 个参考素材。",
            )
        if spec.shape == VideoTaskShape.IMAGE2VIDEO and n_img < 1:
            raise MiniMaxH3ProviderError(
                "图生视频需要至少 1 张图",
                code="MISSING_IMAGE",
                provider=self.name,
                user_message="图生视频需要上传 1 张首帧图片。",
            )
        if spec.duration is not None and not (self.min_duration <= int(spec.duration) <= self.max_duration):
            raise MiniMaxH3ProviderError(
                f"时长须为 {self.min_duration}~{self.max_duration} 秒,当前 {spec.duration}",
                code="UNSUPPORTED_DURATION",
                provider=self.name,
            )

    async def materialize_media(self, ref: MediaRef) -> Optional[str]:
        """URL 透传;bytes 优先走媒体 publisher,否则 data URI。

        参考视频须在透传前钳到 2~15s:http URL 原样上传会被上游拒,或静默用超长原片。
        """
        if ref.kind == MediaKind.VIDEO:
            from ...video_process import prepare_ref_video_if_clamping

            ref = await prepare_ref_video_if_clamping(ref)
        if ref.url:
            return ref.url
        if ref.data is None:
            return None
        mime = ref.mime_type or _default_mime(ref.kind)
        from ....core.media_host import MediaPublishError, materialize

        try:
            published = await materialize(ref.data, mime)
        except MediaPublishError as exc:
            raise MiniMaxH3ProviderError(
                f"媒体外链化失败: {exc}",
                code="MEDIA_PUBLISH_FAILED",
                retryable=True,
                provider=self.name,
                user_message="参考素材上传失败,请稍后重试。",
            ) from exc
        if published:
            return published
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

    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        vendor = (model or self.VENDOR_MODEL).strip() or self.VENDOR_MODEL
        content = await self._build_content(spec)
        body: dict[str, Any] = {
            "model": vendor,
            "content": content,
            "resolution": to_api_resolution(spec.resolution),
            "duration": int(spec.duration or 5),
        }
        ratio = to_api_ratio(spec)
        if ratio:
            body["ratio"] = ratio
        if spec.watermark:
            body["aigc_watermark"] = True
        url = f"{self.base_url}{self.CREATE_PATH}"
        headers = self._headers()
        return "POST", url, headers, body

    async def _build_content(self, spec: VideoGenSpec) -> list[dict[str, Any]]:
        text = _merge_h3_text(spec)
        items: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if spec.shape == VideoTaskShape.TEXT2VIDEO:
            return items
        media = _select_h3_media(spec)
        urls = await self.materialize_all([m.ref for m in media])
        for spec_media, url in zip(media, urls):
            if not url:
                continue
            role = _h3_role(spec_media.role, spec_media.kind)
            kind = spec_media.kind
            if kind == MediaKind.IMAGE:
                item: dict[str, Any] = {"type": "image_url", "image_url": {"url": url}}
            elif kind == MediaKind.VIDEO:
                item = {"type": "video_url", "video_url": {"url": url}}
            else:
                item = {"type": "audio_url", "audio_url": {"url": url}}
            if role:
                item["role"] = role
            items.append(item)
        return items

    def parse_create(self, resp_json: dict[str, Any]) -> str:
        if "task_id" in resp_json and resp_json["task_id"]:
            return str(resp_json["task_id"])
        raw_task = resp_json["task"] if "task" in resp_json else None
        if isinstance(raw_task, dict) and "id" in raw_task and raw_task["id"]:
            return str(raw_task["id"])
        return ""

    async def get(self, task_id: str) -> NormalizedTask:
        url = f"{self.base_url}{self.QUERY_PATH.format(task_id=task_id)}"
        resp = await self._request("GET", url, headers=self._headers())
        return self._parse_task(resp)

    def _parse_task(self, resp_json: dict[str, Any]) -> NormalizedTask:
        raw_task = resp_json["task"] if "task" in resp_json else resp_json
        task = raw_task if isinstance(raw_task, dict) else {}
        raw_status = str(task["status"] if "status" in task else "").lower()
        status = (
            self.STATUS_MAP[raw_status] if raw_status in self.STATUS_MAP else NormalizedStatus.RUNNING
        )
        content = _as_dict(task["content"] if "content" in task else None)
        video_url = content["url"] if "url" in content else None
        error = None
        if status == NormalizedStatus.FAILED:
            err = _as_dict(task["error"] if "error" in task else None)
            code = str(err["code"]) if "code" in err and err["code"] else ""
            msg = str(err["message"]) if "message" in err and err["message"] else "任务失败"
            error = f"{code}: {msg}" if code else msg
        usage_raw = _as_dict(task["usage"] if "usage" in task else None)
        vendor_cost = None
        if "total_seconds" in usage_raw:
            vendor_cost = usage_raw["total_seconds"]
        elif "output_seconds" in usage_raw:
            vendor_cost = usage_raw["output_seconds"]
        usage: dict[str, Any] = {
            "vendor": self.name,
            "vendor_unit": "seconds",
            "vendor_cost": vendor_cost,
            "raw_usage": usage_raw,
        }
        task_id = str(task["id"]) if "id" in task and task["id"] else ""
        return NormalizedTask(
            id=task_id,
            status=status,
            video_url=str(video_url) if video_url else None,
            usage=usage,
            error=error,
            raw=resp_json if isinstance(resp_json, dict) else {},
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = RetryingAsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))
        return self._client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        from ....rh_config.comfyui_config import plugin_dry_run

        if self.dry_run or plugin_dry_run():
            logger.info(
                f"[MiniMaxH3] Dry-Run 拦截(未发送) {method} {url}\n"
                f"  headers: {mask_headers(headers)}\n"
                f"  body:\n{dump_body(mask_body(json))}"
            )
            raise DryRunInterrupt(f"[MiniMaxH3] Dry-Run 已启用,请求未发送: {method} {url}")

        logger.debug(
            f"[MiniMaxH3] 请求 {method} {url}\n"
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
            resp_text = (resp.text or "")[:2000]
        emit = logger.warning if resp.status_code >= 400 else logger.debug
        emit(f"[MiniMaxH3] 响应 {resp.status_code} ({len(resp.content)} bytes)\n  body: {resp_text}")
        if resp.status_code >= 400:
            raise self._build_http_error(resp, resp_json)
        if method.upper() == "DELETE" and (resp_json is None or not (resp.content or b"").strip()):
            return {}
        if isinstance(resp_json, dict):
            return resp_json
        if method.upper() in ("DELETE", "POST") and not (resp.content or b"").strip():
            return {}
        raise MiniMaxH3ProviderError(
            f"MiniMax H3 返回非 JSON: {resp_text[:500]}",
            code="BAD_RESPONSE",
            retryable=True,
            provider=self.name,
            user_message="上游返回格式异常,请稍后重试。",
        )

    def _build_http_error(self, resp: httpx.Response, resp_json: Any) -> MiniMaxH3ProviderError:
        body = resp_json if isinstance(resp_json, dict) else {"raw": (resp.text or "")[:500]}
        vendor_msg = _pick_oai_message(body, http_status=resp.status_code)
        retryable = http_status_retryable(resp.status_code)
        # 参数/审核/余额不可切通道
        if resp.status_code in (400, 402, 422):
            retryable = False
        return MiniMaxH3ProviderError(
            f"MiniMax H3 API 错误 {resp.status_code}: {vendor_msg}",
            code="HTTP_ERROR",
            retryable=retryable,
            provider=self.name,
            http_status=resp.status_code,
            user_message=vendor_msg,
        )

    async def run(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str] = None,
        on_progress: Optional[Callable[[NormalizedTask], Any]] = None,
    ) -> NormalizedTask:
        self.validate_spec(spec)
        from ...video_process import RefVideoClampSpec, use_ref_video_clamp

        # H3 官方单段 [2,15]s、合计 ≤15s;不做 Seedance 407696 像素放大。
        with use_ref_video_clamp(RefVideoClampSpec(min_pixels=0)):
            method, url, headers, body = await self.render_create(spec, model=model)
        masked = mask_body(body)
        logger.info(
            f"[MiniMaxH3] 创建任务: model={model or self.VENDOR_MODEL}, endpoint={url}\n"
            f"  request:\n{dump_body(masked)}"
        )
        from ....core.telemetry.wire_capture import set_wire_from_http_body

        set_wire_from_http_body(masked, prompt=(spec.prompt or "").strip() or None)
        resp = await self._request(method, url, headers=headers, json=body)
        task_id = self.parse_create(resp)
        if not task_id:
            raise MiniMaxH3ProviderError(
                f"MiniMax H3 未返回 task_id: {resp}",
                code="NO_TASK_ID",
                retryable=True,
                provider=self.name,
                user_message="上游未返回任务 ID,请稍后重试。",
            )
        logger.info(f"[MiniMaxH3] 任务已创建: task_id={task_id}")
        await self._bind_active_cancel(task_id)
        try:
            return await self.poll_until_done(task_id, on_progress=on_progress)
        except asyncio.CancelledError:
            await self._best_effort_delete(task_id)
            raise

    async def delete(self, task_id: str) -> None:
        """取消排队中任务,或删除已结束记录。running 态上游会拒绝。"""
        if not task_id:
            return
        url = f"{self.base_url}{self.DELETE_PATH.format(task_id=task_id)}"
        logger.info(f"[MiniMaxH3] 上游 cancel DELETE task_id={task_id} url={url}")
        try:
            await self._request("DELETE", url, headers=self._headers(), json=None)
            logger.info(f"[MiniMaxH3] 上游 cancel 完成 task_id={task_id}")
        except DryRunInterrupt:
            logger.info(f"[MiniMaxH3] Dry-Run 跳过 cancel task_id={task_id}")
            return

    def supports_remote_cancel(self) -> bool:
        """queued 可 DELETE 取消;running 上游拒绝,本地仍会停轮询。"""
        return True

    async def _bind_active_cancel(self, task_id: str) -> None:
        if not task_id:
            return
        try:
            from ....core.dispatch.active_tasks import get_active_task_registry

            provider = self

            async def _cancel_remote() -> None:
                await provider.delete(task_id)

            await get_active_task_registry().bind_vendor_task(
                vendor_task_id=task_id,
                cancel_remote=_cancel_remote,
                channel_name=self.name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[MiniMaxH3] 绑定 vendor 任务失败(忽略): {exc}")

    async def _best_effort_delete(self, task_id: str) -> None:
        if not task_id:
            return
        try:
            from ....core.dispatch.active_tasks import remote_cancel_already_attempted

            if remote_cancel_already_attempted():
                logger.debug(
                    f"[MiniMaxH3] 跳过 CancelledError 兜底 cancel"
                    f"(cancel_generation 已尝试上游): {task_id}"
                )
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.delete(task_id)
            logger.info(f"[MiniMaxH3] CancelledError 兜底已 cancel 上游任务 {task_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[MiniMaxH3] CancelledError 兜底 cancel 失败: {exc}")

    async def poll_until_done(
        self,
        task_id: str,
        *,
        interval: Optional[float] = None,
        max_wait: float = 1800.0,
        heartbeat_every: int = 6,
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
                # Transport retry (5× / 5s) already happened in RetryingAsyncClient.
                elapsed = loop.time() - start
                raise MiniMaxH3ProviderError(
                    f"MiniMax H3 轮询失败({type(poll_exc).__name__}: {poll_exc}); "
                    f"上次状态={last_status}, 已等待={elapsed:.1f}s",
                    code="POLL_NETWORK_ERROR",
                    retryable=False,
                    provider=self.name,
                    user_message="轮询阶段网络异常,请稍后重试。",
                ) from poll_exc

            if task.status != last_status:
                logger.info(f"[MiniMaxH3] 任务 {task_id} 状态变更: {last_status} → {task.status}")
                last_status = task.status
                poll_count = 0
            elif poll_count > 0 and poll_count % heartbeat_every == 0:
                elapsed = loop.time() - start
                logger.info(f"[MiniMaxH3] 任务 {task_id} 轮询中: status={task.status}, 已等待={elapsed:.1f}s")

            if on_progress is not None:
                try:
                    res = on_progress(task)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[MiniMaxH3] 进度回调异常: {exc}")

            if task.status in TERMINAL_STATUSES:
                raw_dump = dump_body(mask_body(task.raw))
                if task.status == NormalizedStatus.FAILED:
                    vendor_msg = task.error or str(task.raw)
                    logger.warning(f"[MiniMaxH3] 任务 {task_id} 失败: {vendor_msg}\n  result:\n{raw_dump}")
                    raise MiniMaxH3ProviderError(
                        vendor_msg,
                        code="TASK_FAILED",
                        retryable=False,
                        provider=self.name,
                        user_message=vendor_msg,
                    )
                if task.status == NormalizedStatus.EXPIRED:
                    raise MiniMaxH3ProviderError(
                        f"任务 {task.status.value}: {task_id}",
                        code="EXPIRED",
                        retryable=False,
                        provider=self.name,
                        user_message=f"任务已{task.status.value}",
                    )
                logger.info(f"[MiniMaxH3] 任务 {task_id} 终态 {task.status.value}\n  result:\n{raw_dump}")
                return task

            if loop.time() - start > max_wait:
                raise MiniMaxH3ProviderError(
                    f"任务轮询超时({max_wait}s): {task_id}",
                    code="POLL_TIMEOUT",
                    retryable=False,
                    provider=self.name,
                    user_message="视频生成超时,请稍后重试或用 resume-poll 继续查询。",
                )
            await asyncio.sleep(poll_iv)


def _merge_h3_text(spec: VideoGenSpec) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    candidates: list[str] = []
    if spec.prompt:
        candidates.append(spec.prompt)
    for seg in spec.ordered_segments:
        if seg.kind == "text" and seg.text:
            candidates.append(seg.text)
    for raw in candidates:
        text = raw.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return "\n".join(parts)


def _select_h3_media(spec: VideoGenSpec) -> list[SpecMedia]:
    if spec.shape in (VideoTaskShape.IMAGE2VIDEO, VideoTaskShape.FIRST_LAST_FRAME):
        wanted = (MediaRole.FIRST_FRAME, MediaRole.LAST_FRAME)
        media = [m for m in spec.images() if m.role in wanted]
        if media:
            return media
        limit = 1 if spec.shape == VideoTaskShape.IMAGE2VIDEO else 2
        return spec.images()[:limit]
    if spec.ordered_segments:
        picked: list[SpecMedia] = []
        for seg in spec.ordered_segments:
            if seg.kind == "media" and seg.media is not None:
                picked.append(seg.media)
        if picked:
            return picked
    return list(spec.images()) + list(spec.videos()) + list(spec.audios())


def _h3_role(role: MediaRole, kind: MediaKind) -> Optional[str]:
    if role == MediaRole.FIRST_FRAME:
        return "first_frame"
    if role == MediaRole.LAST_FRAME:
        return "last_frame"
    if kind == MediaKind.IMAGE:
        return "reference_image"
    if kind == MediaKind.VIDEO:
        return "reference_video"
    if kind == MediaKind.AUDIO:
        return "reference_audio"
    return None


def _default_mime(kind: MediaKind) -> str:
    if kind == MediaKind.IMAGE:
        return "image/jpeg"
    if kind == MediaKind.VIDEO:
        return "video/mp4"
    return "audio/mpeg"


def live_minimax_h3_provider() -> MiniMaxH3Provider:
    """按当前配置构造 provider(测试 / resume 回落)。"""
    return MiniMaxH3Provider(
        api_key=minimax_api_key(),
        dry_run=minimax_dry_run(),
    )


__all__ = [
    "MiniMaxH3Provider",
    "MiniMaxH3ProviderError",
    "live_minimax_h3_provider",
]
