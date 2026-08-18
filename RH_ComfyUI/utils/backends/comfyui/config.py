"""ComfyUI 共用配置:工作流启用列表。

网页控制台 ``ComfyUI_Enabled_Workflows``(GsListStrConfig)决定哪些
ComfyUI 工作流参与分发;默认空 = 全部不启用。可填内部模型名或
workflow json 文件名;每次读取配置,增删即时生效。
"""

from __future__ import annotations

from typing import Any

COMFYUI_WORKFLOW_QWEN_2511 = "qwen_2511"
COMFYUI_WORKFLOW_QWEN_2512 = "qwen_2512"
COMFYUI_WORKFLOW_WAN22 = "wan2.2_videogen"
COMFYUI_WORKFLOW_INDEX_TTS2 = "IndexTTS2"
COMFYUI_WORKFLOW_ACE_STEP = "ace_step1.5"

COMFYUI_WORKFLOW_OPTIONS: tuple[str, ...] = (
    COMFYUI_WORKFLOW_QWEN_2511,
    COMFYUI_WORKFLOW_QWEN_2512,
    COMFYUI_WORKFLOW_WAN22,
    COMFYUI_WORKFLOW_INDEX_TTS2,
    COMFYUI_WORKFLOW_ACE_STEP,
)


def _cfg(key: str) -> Any:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    item = SERVICE_CONFIG.get_config(key)
    return item.data


def comfyui_enabled_workflows() -> list[str]:
    """当前启用项(去空白);默认空。"""
    raw = _cfg("ComfyUI_Enabled_Workflows")
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


def _aliases(token: str) -> set[str]:
    t = (token or "").strip()
    if not t:
        return set()
    aliases = {t}
    if t.lower().endswith(".json"):
        aliases.add(t[:-5])
    else:
        aliases.add(f"{t}.json")
    return aliases


def is_comfyui_workflow_enabled(name: str = "", workflow_file: str = "") -> bool:
    """模型名或 workflow 文件名是否在启用列表中。列表为空则全部关闭。"""
    enabled = comfyui_enabled_workflows()
    if not enabled:
        return False
    allowed: set[str] = set()
    for item in enabled:
        allowed.update(_aliases(item))
    for token in (name, workflow_file):
        if (token or "").strip() in allowed:
            return True
    return False


def comfyui_disabled_reason(name: str, display_name: str) -> str | None:
    if is_comfyui_workflow_enabled(name):
        return None
    label = (name or "").strip() or display_name
    return (
        f"{display_name} 未启用:请在 Web 控制台 ComfyUI 栏的"
        f"「启用的 ComfyUI 工作流」中添加 {label}"
    )


__all__ = [
    "COMFYUI_WORKFLOW_ACE_STEP",
    "COMFYUI_WORKFLOW_INDEX_TTS2",
    "COMFYUI_WORKFLOW_OPTIONS",
    "COMFYUI_WORKFLOW_QWEN_2511",
    "COMFYUI_WORKFLOW_QWEN_2512",
    "COMFYUI_WORKFLOW_WAN22",
    "comfyui_disabled_reason",
    "comfyui_enabled_workflows",
    "is_comfyui_workflow_enabled",
]
