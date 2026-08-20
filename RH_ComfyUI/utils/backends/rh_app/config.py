"""RunningHub AI 应用启用列表。

网页控制台 ``RH_App_Enabled_Apps``(GsListStrConfig)决定哪些 rh_app
模型参与分发。默认勾满当前内置应用;留空则全部关闭。
可填内部模型名或 webappId;每次读取配置,增删即时生效。
"""

from __future__ import annotations

from typing import Any

RH_APP_ANIMA = "anima"
RH_APP_CAMERA_ANGLE = "rh_camera_angle"
RH_APP_IMAGE_MATTING = "rh_image_matting"
RH_APP_IMAGE_UPSCALE = "rh_image_upscale"
RH_APP_IMAGE_OUTPAINT = "rh_image_outpaint"
RH_APP_INDEXTTS25 = "IndexTTS2.5"

RH_APP_OPTIONS: tuple[str, ...] = (
    RH_APP_ANIMA,
    RH_APP_CAMERA_ANGLE,
    RH_APP_IMAGE_MATTING,
    RH_APP_IMAGE_UPSCALE,
    RH_APP_IMAGE_OUTPAINT,
    RH_APP_INDEXTTS25,
)


def _cfg(key: str) -> Any:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    item = SERVICE_CONFIG.get_config(key)
    return item.data


def rh_app_enabled_apps() -> list[str]:
    """当前启用项(去空白)。"""
    raw = _cfg("RH_App_Enabled_Apps")
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


def is_rh_app_enabled(name: str = "", webapp_id: str = "") -> bool:
    """模型名或 webappId 是否在启用列表中。列表为空则全部关闭。"""
    enabled = rh_app_enabled_apps()
    if not enabled:
        return False
    allowed = set(enabled)
    for token in (name, webapp_id):
        if (token or "").strip() in allowed:
            return True
    return False


def rh_app_disabled_reason(name: str, display_name: str) -> str | None:
    if is_rh_app_enabled(name):
        return None
    label = (name or "").strip() or display_name
    return (
        f"{display_name} 未启用:请在 Web 控制台的"
        f"「启用的 RunningHub AI 应用」中添加 {label}"
    )


__all__ = [
    "RH_APP_ANIMA",
    "RH_APP_CAMERA_ANGLE",
    "RH_APP_IMAGE_MATTING",
    "RH_APP_IMAGE_OUTPAINT",
    "RH_APP_IMAGE_UPSCALE",
    "RH_APP_INDEXTTS25",
    "RH_APP_OPTIONS",
    "is_rh_app_enabled",
    "rh_app_disabled_reason",
    "rh_app_enabled_apps",
]
