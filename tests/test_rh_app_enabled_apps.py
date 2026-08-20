"""RunningHub AI 应用启用列表:默认全开、webappId 别名、热读增删。"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.core.base.errors import ChannelError
from RH_ComfyUI.utils.core.request import TaskType
from RH_ComfyUI.utils.core.pipeline import NodeDef
from RH_ComfyUI.utils.backends.rh_app.config import (
    RH_APP_OPTIONS,
    RH_APP_CAMERA_ANGLE,
    is_rh_app_enabled,
    rh_app_disabled_reason,
)


class _OkAdapter:
    async def check_available(self) -> bool:
        return True

    async def get_unavailable_reason(self) -> str:
        return "ok"


def test_enabled_list_default_all_and_webapp_alias(monkeypatch):
    import RH_ComfyUI.utils.backends.rh_app.config as rcfg

    monkeypatch.setattr(
        rcfg,
        "_cfg",
        lambda key: list(RH_APP_OPTIONS) if key == "RH_App_Enabled_Apps" else None,
    )
    assert is_rh_app_enabled("anima") is True
    assert is_rh_app_enabled(RH_APP_CAMERA_ANGLE) is True
    assert is_rh_app_enabled("rh_image_outpaint") is True
    assert is_rh_app_enabled("IndexTTS2.5") is True

    monkeypatch.setattr(rcfg, "_cfg", lambda key: [] if key == "RH_App_Enabled_Apps" else None)
    assert is_rh_app_enabled("anima") is False
    assert rh_app_disabled_reason("anima", "Anima") is not None

    monkeypatch.setattr(
        rcfg,
        "_cfg",
        lambda key: ["rh_image_upscale"] if key == "RH_App_Enabled_Apps" else None,
    )
    assert is_rh_app_enabled("rh_image_upscale") is True
    assert is_rh_app_enabled("anima") is False
    assert is_rh_app_enabled("", "2084945150656212993") is False

    monkeypatch.setattr(
        rcfg,
        "_cfg",
        lambda key: ["2084945150656212993"] if key == "RH_App_Enabled_Apps" else None,
    )
    assert is_rh_app_enabled("rh_image_upscale", "2084945150656212993") is True


def test_adapter_channel_gates_named_rh_app(monkeypatch):
    import RH_ComfyUI.utils.backends.rh_app.config as rcfg

    ch = AdapterChannel(
        "rh_app",
        model_name="rh_image_outpaint",
        workflow_file="2089261625797861377",
    )
    monkeypatch.setattr(ch, "_adapter", lambda: _OkAdapter())

    monkeypatch.setattr(rcfg, "_cfg", lambda key: [] if key == "RH_App_Enabled_Apps" else None)
    assert asyncio.run(ch.check_available()) is False
    reason = asyncio.run(ch.unavailable_reason())
    assert "rh_image_outpaint" in reason

    monkeypatch.setattr(
        rcfg,
        "_cfg",
        lambda key: ["rh_image_outpaint"] if key == "RH_App_Enabled_Apps" else None,
    )
    assert asyncio.run(ch.check_available()) is True

    monkeypatch.setattr(rcfg, "_cfg", lambda key: [] if key == "RH_App_Enabled_Apps" else None)
    node = NodeDef(
        name="rh_image_outpaint",
        display_name="RH 扩图",
        task_type=TaskType("image"),
        backend="rh_app",
        point_cost=1,
        workflow_file="2089261625797861377",
    )
    try:
        asyncio.run(ch.invoke(request=None, node=node))
    except ChannelError as exc:
        assert exc.retryable is True
        assert "rh_image_outpaint" in str(exc)
    else:
        raise AssertionError("disabled RH App must refuse invoke")


def test_unnamed_rh_app_channel_does_not_self_disable(monkeypatch):
    ch = AdapterChannel("rh_app")
    monkeypatch.setattr(ch, "_adapter", lambda: _OkAdapter())
    assert asyncio.run(ch.check_available()) is True


def test_rh_app_model_unavailable_when_disabled(monkeypatch):
    import RH_ComfyUI.utils.backends.rh_app.config as rcfg
    from RH_ComfyUI.models.image.defs import ImageOutpaintDef
    from RH_ComfyUI.models.speech.defs import IndexTTS25Def

    monkeypatch.setattr(rcfg, "_cfg", lambda key: [] if key == "RH_App_Enabled_Apps" else None)
    m = ImageOutpaintDef()
    assert asyncio.run(m.check_available()) is False
    reason = asyncio.run(m.unavailable_reason())
    assert "启用的 RunningHub AI 应用" in reason

    tts = IndexTTS25Def()
    assert asyncio.run(tts.check_available()) is False
    tts_reason = asyncio.run(tts.unavailable_reason())
    assert "IndexTTS2.5" in tts_reason
    assert "启用的 RunningHub AI 应用" in tts_reason
