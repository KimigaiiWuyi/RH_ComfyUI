"""各配置段启用列表:默认勾选 + 闸门。"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from RH_ComfyUI.utils.backends.enabled_list import is_model_enabled
from RH_ComfyUI.utils.backends.seedance.config import is_seedance_model_enabled_on


def _first_keys_after_dividers() -> dict[str, str]:
    keys = list(SERVICE_CONFIG_DEFAULT)
    out: dict[str, str] = {}
    for i, key in enumerate(keys):
        item = SERVICE_CONFIG_DEFAULT[key]
        if type(item).__name__ != "GsDivider":
            continue
        if i + 1 >= len(keys):
            continue
        nxt = keys[i + 1]
        out[key] = nxt
    return out


def test_enable_controls_are_first_in_each_section():
    first = _first_keys_after_dividers()
    assert first["divider_comfyui"] == "ComfyUI_Enabled_Workflows"
    assert first["divider_gemini_image"] == "Gemini_Enable"
    assert first["divider_minimax"] == "MiniMax_Enable"
    assert first["divider_mimo"] == "MIMO_Enable"
    assert first["divider_fishaudio"] == "FishAudio_Enable"
    assert first["divider_seedance"] == "Seedance_Enable_ark"
    assert first["divider_seedance_runninghub"] == "Seedance_Enable_runninghub"
    assert first["divider_openai_image_pool"] == "OpenAI_Image_Enable"
    assert first["divider_happyhorse"] == "HappyHorse_Enable_dashscope"
    assert first["divider_tx_aiart"] == "TX_AIArt_Enable"
    assert "divider_openai_image" not in first
    assert "OpenAI_Image_apikey" not in SERVICE_CONFIG_DEFAULT
    assert "OpenAI_Image_Enabled_Models" not in SERVICE_CONFIG_DEFAULT


def test_every_section_has_model_list_with_defaults():
    lists = [
        "ComfyUI_Enabled_Workflows",
        "RH_App_Enabled_Apps",
        "Gemini_Enabled_Models",
        "MiniMax_Enabled_Models",
        "MIMO_Enabled_Models",
        "FishAudio_Enabled_Models",
        "Seedance_Enabled_Models",
        "Seedance_Enabled_Models_runninghub",
        "DashScope_Enabled_Models",
        "TX_AIArt_Enabled_Models",
    ]
    for key in lists:
        item = SERVICE_CONFIG_DEFAULT[key]
        assert item.data, f"{key} 应带默认模型"
        assert item.options, f"{key} 应有 options"


def test_seedance_vendor_list_gate(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el

    def _cfg(key: str):
        if key == "Seedance_Enabled_Models":
            return ["seedance2"]
        if key == "Seedance_Enabled_Models_runninghub":
            return ["seedance15_pro"]
        return None

    monkeypatch.setattr(el, "_cfg", _cfg)
    assert is_seedance_model_enabled_on("seedance2", "ark") is True
    assert is_seedance_model_enabled_on("seedance2.5", "ark") is False
    assert is_seedance_model_enabled_on("seedance15_pro", "runninghub") is True
    assert is_seedance_model_enabled_on("seedance2", "runninghub") is False


def test_adapter_channel_gates_new_lists(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el
    from RH_ComfyUI.models.bridge import AdapterChannel

    monkeypatch.setattr(el, "_cfg", lambda key: [] if key == "MIMO_Enabled_Models" else None)
    ch = AdapterChannel("mimo", model_name="mimo_tts")
    assert asyncio.run(ch.check_available()) is False
    reason = asyncio.run(ch.unavailable_reason())
    assert "mimo_tts" in reason
    monkeypatch.setattr(el, "_cfg", lambda key: ["mimo_tts"] if key == "MIMO_Enabled_Models" else None)

    class _Ok:
        async def check_available(self) -> bool:
            return True

        async def get_unavailable_reason(self) -> str:
            return "ok"

    monkeypatch.setattr(ch, "_adapter", lambda: _Ok())
    assert asyncio.run(ch.check_available()) is True


def test_is_model_enabled_empty_is_off(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el

    monkeypatch.setattr(el, "_cfg", lambda key: [])
    assert is_model_enabled("MIMO_Enabled_Models", "mimo_tts") is False


def test_vendor_switches_default_on_and_can_disable(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el
    from RH_ComfyUI.utils.backends.enabled_list import is_vendor_enabled
    from RH_ComfyUI.utils.backends.minimax.config import is_minimax_model_enabled
    from RH_ComfyUI.utils.backends.gemini_image.config import is_gemini_model_enabled
    from RH_ComfyUI.utils.backends.openai_image.config import is_openai_image_pool_enabled

    monkeypatch.setattr(el, "_cfg", lambda key: None)
    assert is_vendor_enabled("Gemini_Enable") is True
    assert is_vendor_enabled("TX_AIArt_Enable") is True
    assert is_openai_image_pool_enabled() is True

    def _off(key: str):
        if key.endswith("_Enable") or key == "OpenAI_Image_Enable":
            return False
        if key == "Gemini_Enabled_Models":
            return ["banana2"]
        if key == "MiniMax_Enabled_Models":
            return ["minimax_h3"]
        return None

    monkeypatch.setattr(el, "_cfg", _off)
    import RH_ComfyUI.utils.backends.minimax.config as mcfg
    import RH_ComfyUI.utils.backends.gemini_image.config as gcfg

    monkeypatch.setattr(gcfg, "_cfg", _off)
    monkeypatch.setattr(mcfg, "_cfg", _off)
    assert is_gemini_model_enabled("banana2") is False
    assert is_minimax_model_enabled("minimax_h3") is False
    assert is_openai_image_pool_enabled() is False


def test_tx_vendor_off_blocks_adapter(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el
    from RH_ComfyUI.models.bridge import AdapterChannel

    monkeypatch.setattr(
        el,
        "_cfg",
        lambda key: False if key == "TX_AIArt_Enable" else ["tx_image_outpaint"],
    )
    ch = AdapterChannel("tx_aiart", model_name="tx_image_outpaint")
    assert asyncio.run(ch.check_available()) is False
    assert "启用" in asyncio.run(ch.unavailable_reason())
