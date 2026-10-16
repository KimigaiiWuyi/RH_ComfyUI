"""可灵鉴权。

API Key 直接放进 ``Authorization: Bearer <API Key>``。
没填 API Key 时,用 Access Key + Secret Key 签 HS256 JWT(iss/exp/nbf)。
两种都空则不写 Authorization,避免发出 ``Bearer `` 这种非法头。
"""

from __future__ import annotations

import hmac
import json
import time
import base64
import hashlib

from .config import api_key, access_key, secret_key

_JWT_TTL_S = 1800
_JWT_REFRESH_SKEW_S = 60
_cache: dict[tuple[str, str], tuple[str, int]] = {}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_jwt(ak: str, sk: str, now: int) -> tuple[str, int]:
    """返回 (token, exp)。exp = now + 1800,nbf = now - 5。"""
    exp = now + _JWT_TTL_S
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(json.dumps({"iss": ak, "exp": exp, "nbf": now - 5}, separators=(",", ":")).encode("utf-8"))
    signing = f"{header}.{payload}"
    signature = hmac.new(sk.encode("utf-8"), signing.encode("ascii"), hashlib.sha256).digest()
    return f"{signing}.{_b64url(signature)}", exp


def clear_jwt_cache() -> None:
    _cache.clear()


def cached_jwt(ak: str, sk: str, now: int | None = None) -> str:
    """同一对密钥在到期前 60 秒内复用。密钥变更会落到另一个缓存键。"""
    ts = int(time.time()) if now is None else now
    key = (ak, sk)
    if key in _cache:
        cached, exp = _cache[key]
        if ts < exp - _JWT_REFRESH_SKEW_S:
            return cached
    token, exp = sign_jwt(ak, sk, ts)
    _cache[(ak, sk)] = (token, exp)
    return token


def authorization_header(now: int | None = None) -> str | None:
    """``Bearer <token>``;没有可用凭证时返回 None。"""
    key = api_key()
    if key:
        return f"Bearer {key}"
    ak = access_key()
    sk = secret_key()
    if not ak or not sk:
        return None
    return f"Bearer {cached_jwt(ak, sk, now)}"


__all__ = [
    "authorization_header",
    "cached_jwt",
    "clear_jwt_cache",
    "sign_jwt",
]
