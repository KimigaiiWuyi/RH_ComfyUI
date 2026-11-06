"""Gemini Omni 视频客户端 — 走官方 google-genai SDK 的 Interactions API

与生图(generate_content)不同,Omni 是 Interactions 模型:
``client.aio.interactions.create(model, input, response_format, background=True)``
创建后台任务后按 id 轮询,视频在终态 ``steps[].content[{type:"video"}]``
里以 base64 或 uri 返回。AI Studio / Vertex / 中转 base_url 与生图共用
``Gemini_Image_*`` 配置,客户端构造直接继承。
"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Optional

import httpx
from google.genai import errors as genai_errors

from gsuid_core.logger import logger

from ..gemini_image.api import (
    _TERMINAL_STATUSES,
    GeminiImageAPI,
    _get,
    _download,
    _gemini_vendor_payload,
)
from ....core.channels.polling import PollErrorTolerance
from ....core.telemetry.wire_capture import set_vendor_raw, set_wire_audit


def _find_video(interaction: Any) -> tuple[Optional[bytes], Optional[str]]:
    """在 interaction.steps 里找视频块;返回 (bytes, uri)。

    REST 原始结构:``steps[].content[{type:"video", mime_type, data|uri}]``;
    SDK 对象同样以 ``type``/``data``/``uri`` 字段携带。
    """
    steps = _get(interaction, "steps") or []
    for step in steps:
        content = _get(step, "content") or []
        if isinstance(content, dict):
            content = [content]
        for part in content:
            if str(_get(part, "type") or "").lower() != "video":
                continue
            data = _get(part, "data")
            if data:
                if isinstance(data, (bytes, bytearray)):
                    return bytes(data), None
                return base64.b64decode(str(data)), None
            uri = _get(part, "uri") or _get(part, "url")
            if uri:
                return None, str(uri)
    return None, None


def _interaction_usage(interaction: Any) -> dict[str, Any]:
    """从 interaction 提取 token 用量(没有则空 dict,调用方按估算结算)。"""
    usage = _get(interaction, "usage")
    if not isinstance(usage, dict):
        return {}
    out: dict[str, Any] = {}
    for src, dst in (
        ("total_input_tokens", "prompt_tokens"),
        ("input_tokens", "prompt_tokens"),
        ("prompt_tokens", "prompt_tokens"),
        ("total_output_tokens", "output_tokens"),
        ("output_tokens", "output_tokens"),
        ("video_output_tokens", "video_output_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        val = usage.get(src)
        if val is not None and dst not in out:
            try:
                out[dst] = int(val)
            except (TypeError, ValueError):
                pass
    return out


class GeminiOmniAPI(GeminiImageAPI):
    """Omni 视频生成:Interactions create + 轮询,凭证与生图共用。"""

    vendor_channel_name = "gemini-omni"

    async def create_video(
        self,
        *,
        model: str,
        contents: list[dict[str, Any]],
        aspect_ratio: str,
        resolution: str,
        duration_s: int,
        prompt: str = "",
        max_wait: float = 1800.0,
        poll_interval: float = 3.0,
    ) -> tuple[bytes, dict[str, Any]]:
        """生成视频并返回 (原始字节, usage)。失败抛 RuntimeError(带上游文案)。"""
        client = self._build_client()
        response_format: dict[str, Any] = {
            "type": "video",
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        }
        if duration_s > 0:
            response_format["duration"] = f"{duration_s}s"

        endpoint = self.base_url if (self.base_url and not self.is_vertex) else "官方"
        logger.info(
            f"[Gemini-Omni] interactions.create model={model} vertex={self.is_vertex} "
            f"endpoint={endpoint} ratio={aspect_ratio} res={resolution} dur={duration_s}s "
            f"input={len(contents)} 段"
        )
        set_wire_audit(
            prompt=prompt,
            request={
                "model": model,
                "response_format": response_format,
                "input": [
                    {
                        "type": c.get("type"),
                        "mime_type": c.get("mime_type"),
                        "uri": c.get("uri"),
                        "text": c.get("text"),
                    }
                    for c in contents
                ],
                "api": "interactions",
            },
        )

        interaction = await client.aio.interactions.create(
            model=model,
            input=contents,
            response_format=response_format,
            background=True,
        )
        iid = str(getattr(interaction, "id", None) or "")
        if iid:
            await self._bind_active_cancel(client, iid)

        try:
            interaction = await self._poll_video_until_done(
                client, interaction, interval=poll_interval, max_wait=max_wait
            )
        except asyncio.CancelledError:
            if iid:
                await self._best_effort_cancel(client, iid)
            raise

        set_vendor_raw(_gemini_vendor_payload(interaction))
        status = str(getattr(interaction, "status", None) or "").lower()
        if status in {"failed", "cancelled", "canceled"}:
            raise RuntimeError(f"Gemini Omni interaction 失败: status={status} id={iid}")

        data, uri = _find_video(interaction)
        if data is None and uri:
            logger.info(f"[Gemini-Omni] 视频为外链,下载: {uri}")
            data = await _download(uri)
        if data is None:
            raise RuntimeError(f"Gemini Omni 响应未包含视频: status={status} id={iid}")
        logger.info(f"[Gemini-Omni] 完成 model={model} id={iid} {len(data)}B")
        return data, _interaction_usage(interaction)

    async def resume_video(self, interaction_id: str) -> tuple[bytes, dict[str, Any]]:
        """resume_poll 入口:按 interaction id 继续轮询直至出片。"""
        if not interaction_id:
            raise RuntimeError("缺少 interaction_id")
        client = self._build_client()
        interaction = await client.aio.interactions.get(interaction_id)
        interaction = await self._poll_video_until_done(client, interaction, interval=3.0, max_wait=3600.0)
        set_vendor_raw(_gemini_vendor_payload(interaction))
        status = str(getattr(interaction, "status", None) or "").lower()
        if status in {"failed", "cancelled", "canceled"}:
            raise RuntimeError(f"Gemini Omni interaction 失败: status={status}")
        data, uri = _find_video(interaction)
        if data is None and uri:
            data = await _download(uri)
        if data is None:
            raise RuntimeError(f"Gemini Omni 响应未包含视频: status={status}")
        return data, _interaction_usage(interaction)

    async def _poll_video_until_done(
        self,
        client: Any,
        interaction: Any,
        *,
        interval: float,
        max_wait: float,
    ) -> Any:
        """轮询 background interaction 直至终态(视频版,结构同 _poll_until_done)。"""
        loop = asyncio.get_event_loop()
        start = loop.time()
        iid = str(getattr(interaction, "id", None) or "")
        status = str(getattr(interaction, "status", None) or "").lower()
        if status in _TERMINAL_STATUSES:
            return interaction
        data, uri = _find_video(interaction)
        if data is not None or uri:
            return interaction

        poll_n = 0
        tolerance = PollErrorTolerance(window_s=self.poll_error_window_s)
        while True:
            status = str(getattr(interaction, "status", None) or "").lower()
            if status in _TERMINAL_STATUSES:
                return interaction
            data, uri = _find_video(interaction)
            if data is not None or uri:
                return interaction
            if loop.time() - start > max_wait:
                raise RuntimeError(
                    f"Gemini Omni background interaction 超时({max_wait:.0f}s): id={iid} status={status}"
                )
            await asyncio.sleep(interval)
            poll_n += 1
            if not iid:
                raise RuntimeError("Gemini Omni background interaction 缺少 id,无法轮询")
            try:
                interaction = await client.aio.interactions.get(iid)
            except genai_errors.APIError as exc:
                delay = tolerance.next_delay(http_status=exc.code)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                continue
            except (httpx.HTTPError, asyncio.TimeoutError, OSError):
                delay = tolerance.next_delay(http_status=None)
                if delay is None:
                    raise
                await asyncio.sleep(delay)
                continue
            tolerance.reset()
            if poll_n % 10 == 0:
                logger.info(
                    f"[Gemini-Omni] 轮询 interaction_id={iid} status={getattr(interaction, 'status', None)} n={poll_n}"
                )


gemini_omni_api = GeminiOmniAPI()

__all__ = ["GeminiOmniAPI", "gemini_omni_api"]
