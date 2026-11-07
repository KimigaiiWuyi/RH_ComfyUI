"""fal 的服务配置读取。

每次调用时现读 SERVICE_CONFIG:网页改了 Key / 并发 / 启用模型
立即生效,不需要重启,也不会在 import 时把密钥冻进模块状态。
"""

from __future__ import annotations

from .catalog import FAL_MODEL_NAMES, DEFAULT_QUEUE_ENDPOINT
from ....rh_config.comfyui_config import SERVICE_CONFIG


def _cfg(key: str) -> object:
    try:
        return SERVICE_CONFIG.get_config(key).data
    except KeyError:
        return None


def _text(key: str) -> str:
    data = _cfg(key)
    return data.strip() if isinstance(data, str) else ""


def api_key() -> str:
    return _text("FAL_API_Key")


def endpoint() -> str:
    return (_text("FAL_Endpoint") or DEFAULT_QUEUE_ENDPOINT).rstrip("/")


def enabled_flag() -> bool:
    return bool(_cfg("FAL_Enable"))


def credentials_ready() -> bool:
    return enabled_flag() and bool(api_key())


def enabled_model_names() -> list[str]:
    enabled = _cfg("FAL_Enabled_Models")
    if not isinstance(enabled, list):
        return []
    return [n for n in FAL_MODEL_NAMES if n in enabled]


def is_model_enabled(model_name: str) -> bool:
    return model_name in enabled_model_names()


def max_concurrency() -> int:
    raw = _text("FAL_Max_Concurrency")
    try:
        return max(1, int(raw))
    except ValueError:
        return 2
