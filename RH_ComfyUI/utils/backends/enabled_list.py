"""网页「启用的 xx 模型」列表:热读 SERVICE_CONFIG,空列表 = 全关。"""

from __future__ import annotations

from typing import Any


def _cfg(key: str) -> Any:
    from ...rh_config.comfyui_config import SERVICE_CONFIG

    try:
        return SERVICE_CONFIG.get_config(key).data
    except Exception:
        return None


def enabled_names(config_key: str) -> list[str]:
    raw = _cfg(config_key)
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


def is_vendor_enabled(config_key: str) -> bool:
    """供应商总开关;键缺失或空串视为开启(兼容旧配置)。"""
    raw = _cfg(config_key)
    if raw is None:
        return True
    if isinstance(raw, str) and not raw.strip():
        return True
    return bool(raw)


def vendor_disabled_reason(display_name: str, panel: str) -> str:
    return f"{display_name} 未启用:请在 Web 控制台打开{panel}的供应商开关"


def is_model_enabled(config_key: str, name: str) -> bool:
    target = (name or "").strip()
    if not target:
        return False
    return target in set(enabled_names(config_key))


def disabled_reason(config_key: str, name: str, display_name: str, panel: str) -> str | None:
    if is_model_enabled(config_key, name):
        return None
    return f"{display_name} 未启用:请在 Web 控制台{panel}的启用列表中添加 {name}"


__all__ = [
    "disabled_reason",
    "enabled_names",
    "is_model_enabled",
    "is_vendor_enabled",
    "vendor_disabled_reason",
]
