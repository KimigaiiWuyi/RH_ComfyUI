"""Seedance / Seedream 模型启用列表(ARK 与 RunningHub 分段)。"""

from __future__ import annotations

from ..enabled_list import disabled_reason, is_model_enabled

SEEDANCE_ARK_LIST_KEY = "Seedance_Enabled_Models"
SEEDANCE_RH_LIST_KEY = "Seedance_Enabled_Models_runninghub"

_VENDOR_KEYS = {
    "ark": SEEDANCE_ARK_LIST_KEY,
    "runninghub": SEEDANCE_RH_LIST_KEY,
}


def is_seedance_model_enabled_on(model: str, vendor: str) -> bool:
    key = _VENDOR_KEYS.get((vendor or "").strip().lower())
    if not key:
        return True
    return is_model_enabled(key, model)


def seedance_model_disabled_reason(model: str, display_name: str, vendor: str) -> str | None:
    key = _VENDOR_KEYS.get((vendor or "").strip().lower())
    if not key:
        return None
    panel = "火山官方 Seedance / Seedream 栏" if vendor == "ark" else "RunningHub Seedance 栏"
    return disabled_reason(key, model, display_name, panel)


__all__ = [
    "SEEDANCE_ARK_LIST_KEY",
    "SEEDANCE_RH_LIST_KEY",
    "is_seedance_model_enabled_on",
    "seedance_model_disabled_reason",
]
