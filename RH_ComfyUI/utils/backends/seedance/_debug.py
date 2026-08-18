"""Seedance 调试工具:脱敏 + 完整 body dump。

仅供 `SeedanceProvider._request` 在开启全局 `Dry_Run` 时使用,
用于打印被拦截请求的 headers / body。
"""

from __future__ import annotations

from ...core.safe_json import dump_body, mask_body

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
                masked[k] = f"Bearer {low_v[7:15]}****"
            else:
                masked[k] = f"{low_v[:9]}****"
        else:
            masked[k] = v
    return masked


__all__ = ["mask_headers", "mask_body", "dump_body"]
