"""ComfyUI 工作流启用列表:默认空、模型名/json 别名、热读增删。"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.core.base.errors import ChannelError
from RH_ComfyUI.utils.core.request import TaskType
from RH_ComfyUI.utils.core.pipeline import NodeDef
from RH_ComfyUI.utils.backends.comfyui.config import (
    COMFYUI_WORKFLOW_QWEN_2512,
    comfyui_disabled_reason,
    is_comfyui_workflow_enabled,
)


class _OkAdapter:
    async def check_available(self) -> bool:
        return True

    async def get_unavailable_reason(self) -> str:
        return "ok"


def test_enabled_list_default_empty_and_aliases(monkeypatch):
    import RH_ComfyUI.utils.backends.comfyui.config as ccfg

    monkeypatch.setattr(ccfg, "_cfg", lambda key: [] if key == "ComfyUI_Enabled_Workflows" else None)
    assert is_comfyui_workflow_enabled("qwen_2512", "qwen_2512.json") is False
    assert comfyui_disabled_reason("qwen_2512", "Qwen-Image 2512") is not None

    monkeypatch.setattr(
        ccfg,
        "_cfg",
        lambda key: ["qwen_2512"] if key == "ComfyUI_Enabled_Workflows" else None,
    )
    assert is_comfyui_workflow_enabled("qwen_2512") is True
    assert is_comfyui_workflow_enabled("", "qwen_2512.json") is True
    assert is_comfyui_workflow_enabled("IndexTTS2") is False

    monkeypatch.setattr(
        ccfg,
        "_cfg",
        lambda key: ["qwen_2512.json"] if key == "ComfyUI_Enabled_Workflows" else None,
    )
    assert is_comfyui_workflow_enabled(COMFYUI_WORKFLOW_QWEN_2512, "qwen_2512.json") is True

    monkeypatch.setattr(ccfg, "_cfg", lambda key: [] if key == "ComfyUI_Enabled_Workflows" else None)
    assert is_comfyui_workflow_enabled("qwen_2512") is False


def test_adapter_channel_gates_named_comfyui_workflow(monkeypatch):
    import RH_ComfyUI.utils.backends.comfyui.config as ccfg

    ch = AdapterChannel("comfyui", model_name="qwen_2512", workflow_file="qwen_2512.json")
    monkeypatch.setattr(ch, "_adapter", lambda: _OkAdapter())

    monkeypatch.setattr(ccfg, "_cfg", lambda key: [] if key == "ComfyUI_Enabled_Workflows" else None)
    assert asyncio.run(ch.check_available()) is False
    reason = asyncio.run(ch.unavailable_reason())
    assert "qwen_2512" in reason

    monkeypatch.setattr(
        ccfg,
        "_cfg",
        lambda key: ["qwen_2512"] if key == "ComfyUI_Enabled_Workflows" else None,
    )
    assert asyncio.run(ch.check_available()) is True

    monkeypatch.setattr(ccfg, "_cfg", lambda key: [] if key == "ComfyUI_Enabled_Workflows" else None)
    node = NodeDef(
        name="qwen_2512",
        display_name="Qwen-Image 2512",
        task_type=TaskType("image"),
        backend="comfyui",
        point_cost=1,
        workflow_file="qwen_2512.json",
    )
    try:
        asyncio.run(ch.invoke(request=None, node=node))
    except ChannelError as exc:
        assert exc.retryable is True
        assert "qwen_2512" in str(exc)
    else:
        raise AssertionError("disabled ComfyUI workflow must refuse invoke")


def test_unnamed_comfyui_channel_does_not_self_disable(monkeypatch):
    ch = AdapterChannel("comfyui")
    monkeypatch.setattr(ch, "_adapter", lambda: _OkAdapter())
    assert asyncio.run(ch.check_available()) is True


def test_qwen_model_unavailable_when_workflow_disabled(monkeypatch):
    import RH_ComfyUI.utils.backends.comfyui.config as ccfg
    from RH_ComfyUI.models.image.defs import Qwen2512Def

    monkeypatch.setattr(ccfg, "_cfg", lambda key: [] if key == "ComfyUI_Enabled_Workflows" else None)
    m = Qwen2512Def()
    assert asyncio.run(m.check_available()) is False
    reason = asyncio.run(m.unavailable_reason())
    assert "启用的 ComfyUI 工作流" in reason
