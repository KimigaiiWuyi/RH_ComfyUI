"""Gemini 生图共用配置:模型启用列表。

网页控制台 ``Gemini_Enabled_Models``(GsListStrConfig)决定哪些内部模型
走 Gemini 通道;默认空 = 全部不走 Gemini。banana_pro 的 gpt-image-2 /
外部通道不受此列表影响。
"""

from __future__ import annotations

from typing import Any

GEMINI_MODEL_BANANA1 = "banana1"
GEMINI_MODEL_BANANA2 = "banana2"
GEMINI_MODEL_BANANA_PRO = "banana_pro"

GEMINI_MODEL_OPTIONS: tuple[str, ...] = (
    GEMINI_MODEL_BANANA1,
    GEMINI_MODEL_BANANA2,
    GEMINI_MODEL_BANANA_PRO,
)


def _cfg(key: str) -> Any:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    item = SERVICE_CONFIG.get_config(key)
    return item.data


def gemini_enabled_models() -> list[str]:
    raw = _cfg("Gemini_Enabled_Models")
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


def is_gemini_vendor_enabled() -> bool:
    raw = _cfg("Gemini_Enable")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return True
    return bool(raw)


def is_gemini_model_enabled(name: str) -> bool:
    if not is_gemini_vendor_enabled():
        return False
    target = (name or "").strip()
    if not target:
        return False
    return target in set(gemini_enabled_models())


def gemini_disabled_reason(name: str, display_name: str) -> str | None:
    if not is_gemini_vendor_enabled():
        return f"{display_name} 未启用:请在 Web 控制台打开「启用 Gemini 供应商」"
    if is_gemini_model_enabled(name):
        return None
    return (
        f"{display_name} 未在 Gemini 栏启用:请在 Web 控制台"
        f"「启用的 Gemini 模型」中添加 {name}"
    )


__all__ = [
    "GEMINI_MODEL_BANANA1",
    "GEMINI_MODEL_BANANA2",
    "GEMINI_MODEL_BANANA_PRO",
    "GEMINI_MODEL_OPTIONS",
    "gemini_enabled_models",
    "is_gemini_vendor_enabled",
    "is_gemini_model_enabled",
    "gemini_disabled_reason",
]
