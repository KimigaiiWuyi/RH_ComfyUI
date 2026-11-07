"""fal 异步队列客户端。

队列生命周期:POST 建单 → GET status 轮询 → GET result 取 video.url → 下载。
取消是 PUT(非 DELETE): ``PUT {owner+app}/requests/{request_id}/cancel``。
"""

from __future__ import annotations

from typing import Mapping, Optional

from . import config
from .parse import FalPoll, _object_dict, parse_status, video_url_of, request_id_of, raise_if_error
from .catalog import APP_PREFIX
from .payload import FalSubmit
from ..http_retry import RetryingAsyncClient, download_with_network_retry
from ....core.base.errors import ChannelError
from ....utils.core.types import NodeOutput
from ....core.channels.failure import Phase, Stage, FailureKind
from ....core.channels.polling import PollingChannelMixin
from ....core.telemetry.wire_capture import set_wire_from_http_body

_CHANNEL = "fal"
_CREATE_TIMEOUT = 60
_POLL_TIMEOUT = 30
_RESULT_TIMEOUT = 60
_CANCEL_TIMEOUT = 30


def _headers() -> dict[str, str]:
    """每次调用热读密钥:空 Key 不放 Authorization,让上游 401 说话。"""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = config.api_key()
    if key:
        headers["Authorization"] = f"Key {key}"
    return headers


class FalAPI(PollingChannelMixin):
    """fal 队列式生成接口。

    客户端每次调用新建(配置可能热更新),不缓存实例。
    FLUX 3 Max 生成极快(官方约 1s/1s),轮询间隔比默认可灵短。
    """

    poll_interval_seconds = 2.0

    async def run(self, submit: FalSubmit, *, model_name: str) -> NodeOutput:
        """建单 → 轮询 → 取结果 → 下载,完整走一遍。"""
        # 统计 prompt/request_body 以这条 wire 为准,必须在真实 POST 之前记
        set_wire_from_http_body(submit.body, prompt=submit.prompt or None)
        created = await self._exchange(
            "POST",
            submit.path,
            json_body=submit.body,
            timeout=_CREATE_TIMEOUT,
            stage="create",
        )
        request_id = request_id_of(created)
        await self._bind_vendor(request_id)
        final = await self.poll_until_done(request_id, self.poll_once)
        return await self._to_output(final, request_id=request_id, model_name=model_name)

    async def resume(self, request_id: str, *, model_name: str) -> NodeOutput:
        """已有 request_id,只做轮询和取结果。"""
        final = await self.poll_until_done(request_id, self.poll_once)
        return await self._to_output(final, request_id=request_id, model_name=model_name)

    async def cancel(self, request_id: str) -> None:
        """PUT 取消(仅排队中的任务有效;已开始跑的返回 400)。"""
        await self._exchange(
            "PUT",
            f"/{APP_PREFIX}/requests/{request_id}/cancel",
            timeout=_CANCEL_TIMEOUT,
            stage="other",
        )

    async def poll_once(self, request_id: str) -> tuple[bool, FalPoll]:
        payload = await self._exchange(
            "GET",
            f"/{APP_PREFIX}/requests/{request_id}/status",
            timeout=_POLL_TIMEOUT,
            stage="poll",
        )
        view = parse_status(payload)
        return view.done, view

    async def _bind_vendor(self, request_id: str) -> None:
        """建单成功后把 request_id + 远端取消挂到活动任务注册表。"""
        from ....core.dispatch.active_tasks import get_active_task_registry

        api = self

        async def _cancel() -> None:
            await api.cancel(request_id)

        await get_active_task_registry().bind_vendor_cancel(
            vendor_task_id=request_id,
            cancel_remote=_cancel,
            channel_name=_CHANNEL,
        )

    async def _to_output(self, final: FalPoll, *, request_id: str, model_name: str) -> NodeOutput:
        if final.failed:
            raise ChannelError(
                f"fal 任务失败:{final.message}",
                kind=FailureKind.TASK_FAILED,
                phase=Phase.ACCEPTED,
                channel=_CHANNEL,
                code="TASK_FAILED",
            )
        result = await self._exchange(
            "GET",
            f"/{APP_PREFIX}/requests/{request_id}",
            timeout=_RESULT_TIMEOUT,
            stage="result",
        )
        url = video_url_of(result)
        data = await download_with_network_retry(url, label="fal result")
        if not data:
            raise ChannelError(
                "fal 结果为空",
                kind=FailureKind.TASK_FAILED,
                phase=Phase.ACCEPTED,
                channel=_CHANNEL,
                code="EMPTY_RESULT",
            )
        return NodeOutput(
            status="ok",
            output_type="video",
            data=data,
            mime_type="video/mp4",
            usage={"request_id": request_id, "provider": _CHANNEL, "model": model_name},
            raw={"request_id": request_id, "status": final.raw, "result": result},
            metadata={"task_id": request_id},
        )

    async def _exchange(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, object]] = None,
        timeout: int,
        stage: Stage,
    ) -> dict[str, object]:
        url = f"{config.endpoint()}{path}"
        try:
            async with RetryingAsyncClient(timeout=timeout) as client:
                if method == "GET":
                    response = await client.get(url, headers=_headers())
                elif method == "PUT":
                    response = await client.put(url, headers=_headers())
                else:
                    response = await client.post(url, headers=_headers(), json=json_body)
        except ChannelError:
            raise
        except Exception as exc:
            raise ChannelError.from_transport(exc, stage=stage, channel=_CHANNEL) from exc

        try:
            raw: object = response.json()
        except Exception:
            raw = None
        payload = _object_dict(raw)
        raise_if_error(response.status_code, payload, stage=stage)
        if payload is None:
            raise ChannelError(
                f"fal 响应不是 JSON 对象(HTTP {response.status_code})",
                kind=FailureKind.PROVIDER_FAULT,
                phase=_phase_for(stage),
                http_status=response.status_code,
                channel=_CHANNEL,
                code="BAD_RESPONSE",
                user_message="fal 返回了无法识别的响应,请稍后重试。",
            )
        return payload


def _phase_for(stage: Stage) -> Phase:
    if stage == "create":
        return Phase.REJECTED
    if stage in ("poll", "result"):
        return Phase.ACCEPTED
    return Phase.UNKNOWN
