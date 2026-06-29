"""Seedance 调试工具:脱敏 + 完整 body dump。

仅供 `SeedanceProvider._request` 在开启 `Seedance_Dry_Run` 时使用,
用于打印被拦截请求的 headers / body。
"""

from __future__ import annotations

import re
import json
from typing import Any

# 大小写不敏感的敏感 header 集合。
# 命中这些 key 的 value 在日志里会被替换成 'Bearer ****' 或 '****'。
_SECRET_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "x-api-key",
        "idempotency-key",
        "api-key",
    }
)


# base64 数据 URL 前缀,例如 "data:image/png;base64,"
# 命中后保留前缀,把后面的编码体替换成 `<N chars omitted>`。
_DATA_URL_RE = re.compile(r"^(data:[\w./+-]+;base64,)([A-Za-z0-9+/=\s]+)$")

# 裸 base64 串(>= 256 字符,且全由 base64 字符组成)也按 base64 文本处理。
_BARE_B64_THRESHOLD = 256
_BARE_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")


def mask_headers(headers: dict[str, str] | None) -> dict[str, str]:
    """对敏感 header 做脱敏,其余透传。

    - `Authorization: Bearer xxx` → `Authorization: Bearer ****`
    - 其它敏感 key → `****`
    """
    if not headers:
        return {}
    masked: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _SECRET_HEADERS:
            low_v = v.lower()
            if low_v.startswith("bearer "):
                masked[k] = "Bearer ****"
            else:
                masked[k] = "****"
        else:
            masked[k] = v
    return masked


def _mask_base64_string(value: str) -> str:
    """脱敏单个字符串里的 base64 内容,其余透传。

    - `data:image/png;base64,XXXX` → `data:image/png;base64,<N chars omitted>`
    - 长度 >= 256 且全由 base64 字符组成 → `<base64 len=N>`
    - 其它字符串 → 原样返回
    """
    m = _DATA_URL_RE.match(value)
    if m:
        prefix, payload = m.group(1), m.group(2)
        return f"{prefix}<{len(payload)} chars omitted>"
    if len(value) >= _BARE_B64_THRESHOLD and _BARE_B64_RE.match(value):
        return f"<base64 len={len(value)}>"
    return value


def _mask_node(node: Any) -> Any:
    """递归脱敏树中的字符串 base64 内容;容器结构(list / dict)保持原样。"""
    if isinstance(node, str):
        return _mask_base64_string(node)
    if isinstance(node, list):
        return [_mask_node(x) for x in node]
    if isinstance(node, dict):
        return {k: _mask_node(v) for k, v in node.items()}
    return node


def mask_body(body: Any) -> Any:
    """脱敏 body 里的 base64 字段,结构保持不变。

    设计目的:Seedance 多模态请求的 content[] 里会携带
    `data:image/png;base64,...` 形式的图片,直接 dump 会让 JSON 膨胀到
    上 MB,触发外层 logger 的 4096 字符截断(`…<N chars truncated>`),
    进而把后面所有字段名一并吞掉。先在源头把 base64 替换成占位符,
    再 dump 即可让结构在日志里完整可见。
    """
    return _mask_node(body)


def dump_body(body: Any) -> str:
    """JSON 缩进 dump,中文不被转义,失败时 fallback 到 ``repr``。"""
    try:
        return json.dumps(body, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(body)


__all__ = ["mask_headers", "mask_body", "dump_body"]
