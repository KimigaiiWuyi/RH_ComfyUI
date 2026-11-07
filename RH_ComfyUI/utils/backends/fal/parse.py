"""fal 队列响应解析(纯计算,不打网络)。

队列状态只有 IN_QUEUE / IN_PROGRESS / COMPLETED;
COMPLETED 且带 error / error_type 即任务失败。
结果在 ``GET {owner+app}/requests/{request_id}``,成品在 ``video.url``。
"""

from __future__ import annotations

from dataclasses import dataclass

from ....core.base.errors import ChannelError
from ....core.channels.failure import Phase, Stage, FailureKind

_CHANNEL = "fal"
_PENDING = {"IN_QUEUE", "IN_PROGRESS"}
_SUCCESS = {"COMPLETED"}
# 官方队列状态文档只列三种;预留终态兜底,避免未知终态被当成进行中
_FAILED = {"FAILED", "CANCELLED", "ERROR", "EXPIRED"}


@dataclass(frozen=True)
class FalPoll:
    """一次状态轮询的归一化视图。"""

    done: bool
    failed: bool
    message: str
    raw: dict[str, object]


def _object_dict(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(key, str):
            out[key] = value
    return out


def _detail(payload: dict[str, object]) -> str:
    """fal 错误体的三种形状:detail(422 列表或字符串)/ error / message。"""
    for key in ("detail", "error", "message"):
        if key not in payload:
            continue
        raw = payload[key]
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, list) and raw:
            return "; ".join(str(x) for x in raw)
        if isinstance(raw, dict):
            obj = _object_dict(raw)
            if obj is None:
                continue
            for sub in ("message", "error"):
                if sub not in obj:
                    continue
                val = obj[sub]
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return ""


def raise_if_error(http_status: int, payload: dict[str, object] | None, *, stage: Stage) -> None:
    """把 HTTP 错误翻译成 ChannelError(状态码 → kind 由统一分类推导)。"""
    if http_status < 400:
        return
    detail = _detail(payload) if payload is not None else ""
    message = f"fal HTTP {http_status}:{detail}" if detail else f"fal HTTP {http_status}"
    raise ChannelError.from_http(
        http_status,
        stage=stage,
        channel=_CHANNEL,
        message=message,
        user_message=detail or None,
    )


def request_id_of(payload: object) -> str:
    """建单响应里的 request_id;缺失是厂商契约破坏。"""
    obj = _object_dict(payload)
    if obj is not None and "request_id" in obj:
        raw = obj["request_id"]
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    raise ChannelError(
        "fal 建单响应缺少 request_id",
        kind=FailureKind.PROVIDER_FAULT,
        phase=Phase.REJECTED,
        channel=_CHANNEL,
        code="BAD_RESPONSE",
        user_message="fal 没有返回任务号,请稍后重试。",
    )


def _str_field(payload: dict[str, object], key: str) -> str:
    if key not in payload:
        return ""
    raw = payload[key]
    return raw.strip() if isinstance(raw, str) else ""


def parse_status(payload: object) -> FalPoll:
    """GET .../requests/{id}/status 的响应解析。"""
    obj = _object_dict(payload)
    if obj is None:
        return FalPoll(done=False, failed=False, message="状态响应不是对象", raw={})
    status = _str_field(obj, "status").upper()
    error = _detail(obj)
    if status in _SUCCESS:
        if error:
            return FalPoll(done=True, failed=True, message=error, raw=obj)
        return FalPoll(done=True, failed=False, message="", raw=obj)
    if status in _FAILED:
        return FalPoll(done=True, failed=True, message=error or status, raw=obj)
    queue_pos = obj["queue_position"] if "queue_position" in obj else None
    msg = f"排队中(第 {queue_pos} 位)" if isinstance(queue_pos, int) else "进行中"
    return FalPoll(done=False, failed=False, message=msg, raw=obj)


def video_url_of(payload: object) -> str:
    """结果响应里的 video.url;兼容外层 payload 嵌套。"""
    root = _object_dict(payload)
    if root is not None and "video" not in root and "payload" in root:
        nested = _object_dict(root["payload"])
        if nested is not None:
            root = nested
    if root is not None and "video" in root:
        video = _object_dict(root["video"])
        if video is not None and "url" in video:
            url = video["url"]
            if isinstance(url, str) and url.strip():
                return url.strip()
    raise ChannelError(
        "fal 结果响应缺少 video.url",
        kind=FailureKind.TASK_FAILED,
        phase=Phase.ACCEPTED,
        channel=_CHANNEL,
        code="EMPTY_RESULT",
        user_message="fal 任务完成但未返回视频,请稍后重试。",
    )


def image_of(payload: object) -> tuple[str, str]:
    """结果响应里的 images[0] → (url, mime);兼容外层 payload 嵌套。"""
    root = _object_dict(payload)
    if root is not None and "images" not in root and "payload" in root:
        nested = _object_dict(root["payload"])
        if nested is not None:
            root = nested
    if root is not None and "images" in root:
        raw = root["images"]
        if isinstance(raw, list) and raw:
            first = _object_dict(raw[0])
            if first is not None and "url" in first:
                url = first["url"]
                if isinstance(url, str) and url.strip():
                    mime = first["content_type"] if "content_type" in first else "image/png"
                    return url.strip(), mime if isinstance(mime, str) and mime else "image/png"
    raise ChannelError(
        "fal 结果响应缺少 images[0].url",
        kind=FailureKind.TASK_FAILED,
        phase=Phase.ACCEPTED,
        channel=_CHANNEL,
        code="EMPTY_RESULT",
        user_message="fal 任务完成但未返回图片,请稍后重试。",
    )


__all__ = ["FalPoll", "image_of", "parse_status", "raise_if_error", "request_id_of", "video_url_of"]
