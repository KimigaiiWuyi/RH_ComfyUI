"""供应商凭证脱敏:前 5 位 + 五个星号 + 后 5 位。

统计列 ``backend_key_prefix`` 是 VARCHAR(16),``xxxxx*****xxxxx`` 正好 15 字。
钥长短于 10 时不硬拆后五位,避免几乎整钥落库。
"""

from __future__ import annotations

AUDIT_KEY_HEAD = 5
AUDIT_KEY_TAIL = 5
AUDIT_KEY_STARS = "*****"
AUDIT_KEY_STORE_MAX = 16
_MIN_WITH_TAIL = AUDIT_KEY_HEAD + AUDIT_KEY_TAIL


def is_masked_api_key(value: str) -> bool:
    return AUDIT_KEY_STARS in value.strip()


def mask_api_key(secret: str) -> str:
    """空串保持空;已是掩码则原样截到列宽;否则前 5 + ***** + 后 5。"""
    text = secret.strip()
    if not text:
        return ""
    if AUDIT_KEY_STARS in text:
        return text[:AUDIT_KEY_STORE_MAX]
    if len(text) < _MIN_WITH_TAIL:
        return (text[:AUDIT_KEY_HEAD] + AUDIT_KEY_STARS)[:AUDIT_KEY_STORE_MAX]
    return (text[:AUDIT_KEY_HEAD] + AUDIT_KEY_STARS + text[-AUDIT_KEY_TAIL:])[:AUDIT_KEY_STORE_MAX]
