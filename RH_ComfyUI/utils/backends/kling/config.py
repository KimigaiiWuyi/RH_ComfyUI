"""可灵控制台配置。每次调用现读,不在客户端构造时冻住。"""

from __future__ import annotations

from .catalog import DEFAULT_ENDPOINT, KLING_MODEL_NAMES


def _cfg(key: str) -> object:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    return SERVICE_CONFIG.get_config(key).data


def _text(key: str) -> str:
    raw = _cfg(key)
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _enabled_flag() -> bool:
    return bool(_cfg("Kling_Enable"))


def api_key() -> str:
    return _text("Kling_API_Key")


def access_key() -> str:
    return _text("Kling_Access_Key")


def secret_key() -> str:
    return _text("Kling_Secret_Key")


def endpoint() -> str:
    raw = _text("Kling_Endpoint") or DEFAULT_ENDPOINT
    return raw.rstrip("/")


def max_concurrency() -> int:
    raw = _cfg("Kling_Max_Concurrency")
    if isinstance(raw, bool):
        return 2
    if isinstance(raw, int):
        return max(1, raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.isdigit():
            return max(1, int(text))
    return 2


def credentials_ready() -> bool:
    """启用,且 API Key 或 Access Key + Secret Key 至少一种齐全。"""
    if not _enabled_flag():
        return False
    if api_key():
        return True
    return bool(access_key() and secret_key())


def enabled_model_names() -> set[str]:
    raw = _cfg("Kling_Enabled_Models")
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def is_model_enabled(model_name: str) -> bool:
    if model_name not in KLING_MODEL_NAMES:
        return False
    return model_name in enabled_model_names()


__all__ = [
    "access_key",
    "api_key",
    "credentials_ready",
    "endpoint",
    "is_model_enabled",
    "max_concurrency",
    "secret_key",
]
