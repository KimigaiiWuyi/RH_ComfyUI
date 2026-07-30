"""安全处理请求体中的媒体数据,供日志和任务统计复用。"""

from __future__ import annotations

import re
import enum
import json
import hashlib
import datetime as _datetime
import dataclasses
from typing import Any
from pathlib import Path

# data URL 的 payload 只保留类型和长度/摘要,避免图片/音频内容进入日志或数据库。
_DATA_URL_RE = re.compile(r"^(data:[\w./+\-]+;base64,)([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)
_BARE_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")
_BARE_B64_THRESHOLD = 256

# 这些字段即使内容较短也按 base64 处理。短 base64 图片在长度阈值之下时,
# 不能因为看起来像普通字符串而被写进任务统计表。
_BASE64_FIELD_NAMES = frozenset(
    {
        "base64",
        "data_base64",
        "image_base64",
        "video_base64",
        "audio_base64",
        "b64",
        "b64_json",
        "encoded_data",
    }
)
_NORMALIZED_BASE64_FIELD_NAMES = frozenset(re.sub(r"[^a-z0-9]", "", x) for x in _BASE64_FIELD_NAMES)
# 新格式 <base64://{sha10}#{len}>；兼容旧 <base64 len=N>
_MASKED_BASE64_RE = re.compile(r"^(?:<base64 len=\d+>|<base64://[0-9a-f]+#\d+>)$", re.IGNORECASE)
_MASKED_BYTES_RE = re.compile(r"^<bytes len=\d+>$")
_MASKED_DATA_URL_RE = re.compile(
    r"^data:[\w./+\-]+;base64,(?:<\d+ chars omitted>|<base64://[0-9a-f]+#\d+>)$",
    re.IGNORECASE,
)


def _base64_token(payload: str) -> str:
    """与 canvas_backend.utils.params_sanitize 同形：``<base64://{sha10}#{len}>``。"""
    compact = re.sub(r"\s+", "", payload)
    digest = hashlib.sha256(compact.encode("ascii", errors="ignore")).hexdigest()[:10]
    return f"<base64://{digest}#{len(compact)}>"


def _is_base64_field(name: str | None) -> bool:
    if not name:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", name.lower())
    if normalized in _NORMALIZED_BASE64_FIELD_NAMES:
        return True
    return normalized.endswith("base64") or normalized.endswith("b64json")


def _mask_base64_string(value: str, *, force: bool = False) -> str:
    """替换字符串中的 base64 内容,保留非媒体文本原样。"""
    if _MASKED_BASE64_RE.fullmatch(value) or _MASKED_BYTES_RE.fullmatch(value) or _MASKED_DATA_URL_RE.fullmatch(value):
        return value
    match = _DATA_URL_RE.match(value)
    if match:
        prefix, payload = match.groups()
        return f"{prefix}{_base64_token(payload)}"
    if force:
        return _base64_token(value)
    if len(value) >= _BARE_B64_THRESHOLD and _BARE_B64_RE.fullmatch(value):
        return _base64_token(value)
    return value


def _json_key(key: Any) -> str:
    """把非字符串 dict key 转成稳定的 JSON key。"""
    if isinstance(key, str):
        return key
    if isinstance(key, enum.Enum):
        return str(key.value)
    return str(key)


def _normalize(node: Any, *, seen: set[int], field_name: str | None = None) -> Any:
    """递归转换为 JSON-compatible 值,同时剔除二进制和 base64 内容。"""
    if node is None or isinstance(node, (bool, int, float)):
        return node

    # Enum 必须在 str 之前处理,否则 str-Enum 会保留类名而不是 value。
    if isinstance(node, enum.Enum):
        return _normalize(node.value, seen=seen, field_name=field_name)

    if isinstance(node, str):
        return _mask_base64_string(node, force=_is_base64_field(field_name))

    if isinstance(node, (bytes, bytearray, memoryview)):
        return f"<bytes len={len(node)}>"

    if isinstance(node, Path):
        return str(node)
    if isinstance(node, (_datetime.datetime, _datetime.date, _datetime.time)):
        return node.isoformat()

    # 所有容器/对象都做环引用保护。遇到循环时保留可读占位,不能让统计失败。
    is_container = isinstance(node, (dict, list, tuple, set, frozenset)) or dataclasses.is_dataclass(node)
    marker = id(node)
    if is_container:
        if marker in seen:
            return "<circular reference>"
        seen.add(marker)

    try:
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            return {
                field.name: _normalize(
                    getattr(node, field.name),
                    seen=seen,
                    field_name=field.name,
                )
                for field in dataclasses.fields(node)
            }

        if isinstance(node, dict):
            return {
                _json_key(key): _normalize(value, seen=seen, field_name=_json_key(key)) for key, value in node.items()
            }

        if isinstance(node, (list, tuple, set, frozenset)):
            return [_normalize(value, seen=seen, field_name=field_name) for value in node]

        # params/extra 可能包含第三方对象。优先使用常见的结构化导出方法,
        # 失败时只保留 repr,而不是让 bytes 或对象地址破坏 JSON 序列化。
        for method_name in ("model_dump", "dict"):
            method = getattr(node, method_name, None)
            if callable(method):
                try:
                    return _normalize(method(), seen=seen, field_name=field_name)
                except Exception:  # noqa: BLE001 - 统计兜底不能影响任务
                    pass
        return str(node)
    finally:
        if is_container:
            seen.discard(marker)


def mask_body(body: Any) -> Any:
    """返回脱敏后的请求体,保留结构并保证结果可 JSON 序列化。"""
    return _normalize(body, seen=set())


def dump_body(body: Any) -> str:
    """将请求体完整序列化为 JSON,不对普通字段做整体截断。"""
    return json.dumps(mask_body(body), ensure_ascii=False, separators=(",", ":"))


__all__ = ["mask_body", "dump_body"]
