"""可灵 HTTP 客户端。凭证每次请求现读;真正 POST 之前写入 wire。"""

from __future__ import annotations

import json
from typing import Mapping

import httpx

from gsuid_core.logger import logger

from .auth import authorization_header
from .parse import (
    KlingPoll,
    parse_poll,
    as_object_dict,
    raise_if_error,
    created_task_id,
    decode_vendor_task,
    encode_vendor_task,
)
from .config import endpoint
from .payload import KlingSubmit
from ..http_retry import RetryingAsyncClient, download_with_network_retry
from ...core.types import NodeOutput
from ....core.base.errors import ChannelError
from ....core.channels.polling import PollingChannelMixin
from ....core.telemetry.wire_capture import set_wire_from_http_body

_CHANNEL = "kling"
_CREATE_TIMEOUT = httpx.Timeout(60.0)
_POLL_TIMEOUT = httpx.Timeout(60.0)


class KlingAPI(PollingChannelMixin):
    """创建、轮询、下载。没有上游取消接口。"""

    async def run(self, submit: KlingSubmit, *, model_name: str) -> NodeOutput:
        set_wire_from_http_body(submit.body, prompt=submit.prompt or None)
        created = await self._exchange("POST", submit.path, json_body=submit.body, timeout=_CREATE_TIMEOUT)
        task_id = created_task_id(created)
        encoded = encode_vendor_task(submit.poll_token, task_id)
        from ....core.dispatch.active_tasks import get_active_task_registry

        await get_active_task_registry().bind_vendor_cancel(
            vendor_task_id=encoded,
            cancel_remote=None,
            channel_name=_CHANNEL,
        )
        final = await self.poll_until_done(encoded, self.poll_once)
        return await self._to_output(final, encoded=encoded, task_id=task_id, model_name=model_name, kind=submit.kind)

    async def resume(self, encoded: str, *, model_name: str, kind: str) -> NodeOutput:
        final = await self.poll_until_done(encoded, self.poll_once)
        task_id = decode_vendor_task(encoded)[1]
        return await self._to_output(final, encoded=encoded, task_id=task_id, model_name=model_name, kind=kind)

    async def poll_once(self, encoded: str) -> tuple[bool, KlingPoll]:
        token, task_id = decode_vendor_task(encoded)
        path = self._poll_path(token, task_id)
        if token == "v":
            payload = await self._exchange("GET", path, query={"task_ids": task_id}, timeout=_POLL_TIMEOUT)
        else:
            payload = await self._exchange("GET", path, timeout=_POLL_TIMEOUT)
        view = parse_poll(payload)
        return view.done, view

    async def _to_output(
        self,
        final: KlingPoll,
        *,
        encoded: str,
        task_id: str,
        model_name: str,
        kind: str,
    ) -> NodeOutput:
        if not isinstance(final, KlingPoll):
            raise ChannelError(
                "可灵轮询结果异常",
                retryable=False,
                channel=_CHANNEL,
                user_message="可灵任务结果异常,请稍后重试。",
            )
        if final.failed:
            raise ChannelError(
                final.message or "可灵任务失败",
                retryable=False,
                channel=_CHANNEL,
                code="TASK_FAILED",
                user_message=final.message or "可灵任务失败。",
            )
        if not final.urls:
            raise ChannelError(
                "可灵任务成功但没有结果地址",
                retryable=False,
                channel=_CHANNEL,
                code="EMPTY_RESULT",
                user_message="可灵没有返回结果地址,请稍后重试。",
            )
        primary = final.urls[0]
        data = await download_with_network_retry(primary, label="kling result")
        mime = "video/mp4" if kind == "video" else _image_mime(primary)
        return NodeOutput(
            status="ok",
            output_type=kind,
            data=data,
            mime_type=mime,
            usage={"task_id": task_id, "provider": _CHANNEL, "model": model_name},
            raw={"task_id": task_id, "urls": list(final.urls)},
            metadata={"task_id": encoded, "result_urls": list(final.urls)},
        )

    async def _exchange(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, object] | None = None,
        query: Mapping[str, str] | None = None,
        timeout: httpx.Timeout,
    ) -> dict[str, object]:
        url = f"{endpoint()}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        auth = authorization_header()
        if auth:
            headers["Authorization"] = auth
        logger.info(f"[Kling] {method} {path}")
        try:
            async with RetryingAsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, headers=headers, json=json_body, params=query)
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise ChannelError(
                f"可灵网络错误: {exc}",
                retryable=True,
                transient=True,
                channel=_CHANNEL,
                code="PROVIDER_NETWORK_ERROR",
                user_message="可灵网络异常,请稍后重试。",
            ) from exc
        try:
            raw: object = response.json()
        except json.JSONDecodeError as exc:
            raise ChannelError(
                f"可灵响应不是 JSON(HTTP {response.status_code})",
                retryable=True,
                channel=_CHANNEL,
                user_message="可灵返回了无法识别的响应,请稍后重试。",
            ) from exc
        payload = as_object_dict(raw)
        raise_if_error(response.status_code, payload)
        return payload

    @staticmethod
    def _poll_path(token: str, task_id: str) -> str:
        from .catalog import POLL_PATHS

        base = POLL_PATHS[token] if token in POLL_PATHS else POLL_PATHS["v"]
        if token == "v":
            return base
        return f"{base}/{task_id}"


def _image_mime(url: str) -> str:
    lowered = url.split("?", 1)[0].lower()
    if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
        return "image/jpeg"
    if lowered.endswith(".webp"):
        return "image/webp"
    return "image/png"


__all__ = ["KlingAPI"]
