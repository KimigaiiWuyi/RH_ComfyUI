"""DashScope 共用配置:复用 HappyHorse_* 凭证 + 模型启用列表。

网页控制台 ``DashScope_Enabled_Models``(GsListStrConfig)决定哪些内部
模型走 DashScope;列表为空则全部关闭。API Key / Base URL / 供应商总开关
仍读 ``HappyHorse_apikey_dashscope`` / ``HappyHorse_BaseURL_dashscope`` /
``HappyHorse_Enable_dashscope``,改完即时生效。
"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import dataclass

DASHSCOPE_MODEL_HAPPYHORSE = "happyhorse1.1"
DASHSCOPE_MODEL_WAN30 = "wan3.0"

DASHSCOPE_MODEL_OPTIONS: tuple[str, ...] = (
    DASHSCOPE_MODEL_HAPPYHORSE,
    DASHSCOPE_MODEL_WAN30,
)

DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


@dataclass(frozen=True)
class ProviderCredentials:
    enabled: bool = False
    api_key: str = ""
    base_url: Optional[str] = None


def _cfg(key: str) -> Any:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    try:
        return SERVICE_CONFIG.get_config(key).data
    except Exception:
        return None


def dashscope_credentials() -> ProviderCredentials:
    """读 HappyHorse_*_dashscope 键(万相 3.0 复用同一套 Key)。"""
    enabled = bool(_cfg("HappyHorse_Enable_dashscope"))
    api_key = str(_cfg("HappyHorse_apikey_dashscope") or "")
    base_url = str(_cfg("HappyHorse_BaseURL_dashscope") or "").strip() or None
    return ProviderCredentials(
        enabled=enabled,
        api_key=api_key,
        base_url=base_url or DASHSCOPE_DEFAULT_BASE_URL,
    )


def dashscope_enabled_models() -> list[str]:
    """当前启用的内部模型名(去空白);默认空则全关。"""
    raw = _cfg("DashScope_Enabled_Models")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    out: list[str] = []
    for item in raw:
        name = str(item or "").strip()
        if name:
            out.append(name)
    return out


def is_dashscope_model_enabled(name: str) -> bool:
    """``name`` 是否出现在启用列表中。列表为空则全部关闭。"""
    target = (name or "").strip()
    if not target:
        return False
    return target in set(dashscope_enabled_models())


def dashscope_disabled_reason(name: str, display_name: str) -> str | None:
    """未勾选时返回人话原因;已启用返回 None。"""
    if is_dashscope_model_enabled(name):
        return None
    return (
        f"{display_name} 未启用:请在 Web 控制台 DashScope 栏的"
        f"「启用的 DashScope 模型」中添加 {name}"
    )


__all__ = [
    "DASHSCOPE_DEFAULT_BASE_URL",
    "DASHSCOPE_MODEL_HAPPYHORSE",
    "DASHSCOPE_MODEL_OPTIONS",
    "DASHSCOPE_MODEL_WAN30",
    "ProviderCredentials",
    "dashscope_credentials",
    "dashscope_disabled_reason",
    "dashscope_enabled_models",
    "is_dashscope_model_enabled",
]
