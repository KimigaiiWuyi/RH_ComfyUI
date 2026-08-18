"""MiniMax 共用配置:API Key 热读 + 模型启用列表。

网页控制台 ``MiniMax_Enabled_Models``(GsListStrConfig)决定哪些 MiniMax
模型参与分发;默认空列表 = 全部不启用。语音 / 图片 / H3 视频共用
``MiniMax_apikey``。
"""

from __future__ import annotations

from typing import Any

MINIMAX_MODEL_SPEECH = "minimax_t2a_speech"
MINIMAX_MODEL_IMAGE = "minimax_image01"
MINIMAX_MODEL_H3 = "minimax_h3"

MINIMAX_MODEL_OPTIONS: tuple[str, ...] = (
    MINIMAX_MODEL_SPEECH,
    MINIMAX_MODEL_IMAGE,
    MINIMAX_MODEL_H3,
)


def _cfg(key: str) -> Any:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    item = SERVICE_CONFIG.get_config(key)
    return item.data


def minimax_api_key() -> str:
    """动态读取 API Key,禁止缓存在实例属性上。"""
    return str(_cfg("MiniMax_apikey") or "")


def minimax_enabled_models() -> list[str]:
    """当前启用的内部模型名(去空白);默认空。"""
    raw = _cfg("MiniMax_Enabled_Models")
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


def is_minimax_model_enabled(name: str) -> bool:
    """``name`` 是否出现在启用列表中。列表为空则全部关闭。"""
    target = (name or "").strip()
    if not target:
        return False
    return target in set(minimax_enabled_models())


def minimax_disabled_reason(name: str, display_name: str) -> str | None:
    """未勾选时返回人话原因;已启用返回 None。"""
    if is_minimax_model_enabled(name):
        return None
    return (
        f"{display_name} 未启用:请在 Web 控制台 MiniMax 栏的"
        f"「启用的 MiniMax 模型」中添加 {name}"
    )


def minimax_dry_run() -> bool:
    from ....rh_config.comfyui_config import plugin_dry_run

    return plugin_dry_run()


__all__ = [
    "MINIMAX_MODEL_SPEECH",
    "MINIMAX_MODEL_IMAGE",
    "MINIMAX_MODEL_H3",
    "MINIMAX_MODEL_OPTIONS",
    "minimax_api_key",
    "minimax_enabled_models",
    "is_minimax_model_enabled",
    "minimax_disabled_reason",
    "minimax_dry_run",
]
