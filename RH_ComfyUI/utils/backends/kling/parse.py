"""可灵响应解析。新视频状态是 succeeded,旧图片状态是 succeed。"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import POLL_PATHS, POLL_TASKS
from ....core.base.errors import ChannelError

_CHANNEL = "kling"
_SUCCESS = {"succeeded", "succeed"}
_PENDING = {"submitted", "processing"}
_FAILED = {"failed"}


@dataclass(frozen=True)
class KlingPoll:
    done: bool
    failed: bool
    message: str
    urls: list[str]
    raw: dict[str, object]


def encode_vendor_task(token: str, task_id: str) -> str:
    return f"{token}:{task_id}"


def decode_vendor_task(encoded: str) -> tuple[str, str]:
    """没有已知前缀时按新视频任务查 /tasks。"""
    if ":" in encoded:
        token, task_id = encoded.split(":", 1)
        if token in POLL_PATHS and task_id:
            return token, task_id
    return POLL_TASKS, encoded


def _object_dict(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(key, str):
            out[key] = value
    return out


def _code(payload: dict[str, object]) -> int | None:
    if "code" not in payload:
        return None
    raw = payload["code"]
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return None


def _message(payload: dict[str, object]) -> str:
    if "message" in payload and isinstance(payload["message"], str):
        return payload["message"]
    return ""


def _flags(code: int, http_status: int) -> tuple[bool, bool]:
    """返回 (retryable, transient)。参数错误不重试,限流和 5xx 可重试。"""
    if code in range(1302, 1305) or code in {1100, 1101, 1102} or http_status in {429, 503}:
        return True, True
    if code in {5000, 5001, 5002} or http_status in {500, 502, 504}:
        transient = http_status in {503, 504} or code == 5002
        return True, transient
    return False, False


def raise_if_error(http_status: int, payload: dict[str, object]) -> None:
    code = _code(payload)
    if http_status == 200 and code == 0:
        return
    business = code if code is not None else http_status
    retryable, transient = _flags(business if code is not None else 0, http_status)
    if http_status in {429, 503}:
        retryable, transient = True, True
    text = _message(payload) or f"HTTP {http_status}"
    raise ChannelError(
        f"可灵 {business}: {text}",
        retryable=retryable,
        transient=transient,
        channel=_CHANNEL,
        code=str(business),
        user_message=text,
    )


def created_task_id(payload: dict[str, object]) -> str:
    data = _object_dict(payload["data"]) if "data" in payload else None
    if data is None:
        raise ChannelError(
            "可灵创建响应缺少 data",
            retryable=True,
            channel=_CHANNEL,
            user_message="可灵没有返回任务号,请稍后重试。",
        )
    raw = data["id"] if "id" in data else data["task_id"] if "task_id" in data else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    raise ChannelError(
        "可灵创建响应缺少任务号",
        retryable=True,
        channel=_CHANNEL,
        user_message="可灵没有返回任务号,请稍后重试。",
    )


def _task_record(payload: dict[str, object]) -> dict[str, object] | None:
    if "data" not in payload:
        return None
    data = payload["data"]
    if isinstance(data, list):
        for item in data:
            found = _object_dict(item)
            if found is not None:
                return found
        return None
    return _object_dict(data)


def _str_value(record: dict[str, object], key: str) -> str | None:
    # 下标每次都是新的 object。先收进局部变量，isinstance 才能收窄。
    if key not in record:
        return None
    raw = record[key]
    if isinstance(raw, str):
        return raw
    return None


def _list_value(record: dict[str, object], key: str) -> list[object] | None:
    if key not in record:
        return None
    raw = record[key]
    if not isinstance(raw, list):
        return None
    items: list[object] = []
    for item in raw:
        items.append(item)
    return items


def _status_of(record: dict[str, object]) -> str:
    for key in ("status", "task_status"):
        raw = _str_value(record, key)
        if raw is not None:
            return raw.strip().lower()
    return ""


def _push_url(urls: list[str], raw: object) -> None:
    if isinstance(raw, str) and raw.strip():
        urls.append(raw.strip())


def _urls_from_output_item(item: dict[str, object], urls: list[str]) -> None:
    _push_url(urls, item["url"] if "url" in item else None)
    _push_url(urls, item["url_1"] if "url_1" in item else None)
    _push_url(urls, item["url_2"] if "url_2" in item else None)
    _push_url(urls, item["url_3"] if "url_3" in item else None)


def _collect_urls(record: dict[str, object]) -> list[str]:
    videos: list[str] = []
    others: list[str] = []
    outputs = _list_value(record, "outputs")
    if outputs is not None:
        for item in outputs:
            obj = _object_dict(item)
            if obj is None:
                continue
            bucket = videos if "type" in obj and obj["type"] == "video" else others
            _urls_from_output_item(obj, bucket)
    # 同时有视频和图片时,主产物用视频,图片仍留在结果地址里。
    urls = [*videos, *others]
    result = _object_dict(record["task_result"]) if "task_result" in record else None
    if result is not None:
        for key in ("images", "series_images"):
            images = _list_value(result, key)
            if images is None:
                continue
            for item in images:
                obj = _object_dict(item)
                if obj is not None:
                    _urls_from_output_item(obj, urls)
    return urls


def parse_poll(payload: dict[str, object]) -> KlingPoll:
    record = _task_record(payload)
    if record is None:
        return KlingPoll(done=False, failed=False, message="", urls=[], raw=payload)
    status = _status_of(record)
    message = ""
    for key in ("message", "task_status_msg"):
        raw = _str_value(record, key)
        if raw is not None:
            message = raw
            break
    if status in _PENDING or not status:
        return KlingPoll(done=False, failed=False, message=message, urls=[], raw=payload)
    if status in _FAILED:
        return KlingPoll(done=True, failed=True, message=message or "任务失败", urls=[], raw=payload)
    if status in _SUCCESS:
        return KlingPoll(done=True, failed=False, message=message, urls=_collect_urls(record), raw=payload)
    return KlingPoll(done=False, failed=False, message=message, urls=[], raw=payload)


def as_object_dict(raw: object) -> dict[str, object]:
    found = _object_dict(raw)
    if found is None:
        raise ChannelError(
            "可灵响应不是 JSON 对象",
            retryable=True,
            channel=_CHANNEL,
            user_message="可灵返回了无法识别的响应,请稍后重试。",
        )
    return found


__all__ = [
    "KlingPoll",
    "as_object_dict",
    "created_task_id",
    "decode_vendor_task",
    "encode_vendor_task",
    "parse_poll",
    "raise_if_error",
]
