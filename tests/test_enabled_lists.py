"""各配置段启用列表:默认勾选 + 闸门。"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsListStrConfig, GsRepeatGroupConfig
from RH_ComfyUI.utils.backends.enabled_list import is_model_enabled
from RH_ComfyUI.utils.backends.seedance.config import is_seedance_model_enabled_on


def _keys_after_dividers() -> dict[str, list[str]]:
    keys = list(SERVICE_CONFIG_DEFAULT)
    out: dict[str, list[str]] = {}
    current: str | None = None
    for key in keys:
        item = SERVICE_CONFIG_DEFAULT[key]
        if type(item).__name__ == "GsDivider":
            current = key
            out[current] = []
        elif current is not None:
            out[current].append(key)
    return out


def test_enable_first_and_base_url_second_in_each_section():
    sections = _keys_after_dividers()
    assert sections["divider_comfyui"][:4] == [
        "ComfyUI_Enable",
        "ComfyUI_BaseURL",
        "RH_BaseURL",
        "ComfyUI_Enabled_Workflows",
    ]
    assert sections["divider_gemini_image"][:2] == ["Gemini_Enable", "Gemini_Image_BaseURL"]
    assert sections["divider_minimax"][:2] == ["MiniMax_Enable", "MiniMax_BaseURL"]
    assert sections["divider_mimo"][:2] == ["MIMO_Enable", "MIMO_BaseURL"]
    assert sections["divider_fishaudio"][:2] == ["FishAudio_Enable", "FishAudio_BaseURL"]
    assert sections["divider_seedance"][:2] == ["Seedance_Enable_ark", "Seedance_BaseURL_ark"]
    assert sections["divider_seedance_runninghub"][:2] == [
        "Seedance_Enable_runninghub",
        "Seedance_BaseURL_runninghub",
    ]
    assert sections["divider_openai_image_pool"][:2] == ["OpenAI_Image_Enable", "OpenAI_Image_Providers"]
    assert sections["divider_azure_image"][:2] == ["Azure_Image_Enable", "Azure_Image_Endpoint"]
    assert sections["divider_kling"][:2] == ["Kling_Enable", "Kling_Endpoint"]
    assert sections["divider_fal"][:2] == ["FAL_Enable", "FAL_Endpoint"]
    assert sections["divider_happyhorse"][:2] == [
        "HappyHorse_Enable_dashscope",
        "HappyHorse_BaseURL_dashscope",
    ]
    assert sections["divider_tx_aiart"][:2] == ["TX_AIArt_Enable", "TX_AIArt_BaseURL"]
    assert "divider_openai_image" not in sections
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
        "Azure_Image_Deployment",
        "Kling_Enabled_Models",
        "DashScope_Enabled_Models",
        "TX_AIArt_Enabled_Models",
    ]
    for key in lists:
        item = SERVICE_CONFIG_DEFAULT[key]
        assert isinstance(item, GsListStrConfig), f"{key} 应为 GsListStrConfig"
        assert item.data, f"{key} 应带默认模型"
        assert item.options, f"{key} 应有 options"


def test_gemini_enabled_list_keeps_banana2_and_21():
    item = SERVICE_CONFIG_DEFAULT["Gemini_Enabled_Models"]
    assert isinstance(item, GsListStrConfig)
    assert item.options == [
        "banana1",
        "banana2",
        "banana2.1",
        "banana2.lite",
        "banana_pro",
        "gemini_tts",
        "gemini_omni12",
    ]
    assert item.data == item.options
    pool = SERVICE_CONFIG_DEFAULT["OpenAI_Image_Providers"]
    assert isinstance(pool, GsRepeatGroupConfig)
    models = pool.template["models"]
    assert isinstance(models, GsRepeatGroupConfig)
    real_name = models.template["model_real_name"]
    assert isinstance(real_name, GsStrConfig)
    names = real_name.options
    assert "banana2" in names and "banana2.1" in names and "banana2.lite" in names


def test_fish_enabled_list_is_upstream_tiers():
    from RH_ComfyUI.rh_config.fish_models import FISH_MODEL_NAMES

    assert "FishAudio_Model" not in SERVICE_CONFIG_DEFAULT
    item = SERVICE_CONFIG_DEFAULT["FishAudio_Enabled_Models"]
    assert isinstance(item, GsListStrConfig)
    assert item.options == FISH_MODEL_NAMES
    assert item.data == FISH_MODEL_NAMES


def test_fish_legacy_enabled_names_expand(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el

    monkeypatch.setattr(
        el,
        "_cfg",
        lambda key: ["fish_tts", "fish_asr"] if key == "FishAudio_Enabled_Models" else None,
    )
    monkeypatch.setattr(el, "_legacy_fish_tier", lambda: "s2-pro")
    assert el.enabled_names("FishAudio_Enabled_Models") == ["s2-pro", "transcribe-1"]

    monkeypatch.setattr(el, "_legacy_fish_tier", lambda: "s1")
    assert el.enabled_names("FishAudio_Enabled_Models") == ["s2.1-pro", "transcribe-1"]

    monkeypatch.setattr(el, "_legacy_fish_tier", lambda: "s2.1-pro-free")
    assert el.enabled_names("FishAudio_Enabled_Models") == ["s2.1-pro-free", "transcribe-1"]


def test_fish_new_enabled_names_stay(monkeypatch):
    import RH_ComfyUI.utils.backends.enabled_list as el

    wanted = ["drama-3-preview", "transcribe-1-pro"]
    monkeypatch.setattr(el, "_cfg", lambda key: wanted if key == "FishAudio_Enabled_Models" else None)
    monkeypatch.setattr(el, "_legacy_fish_tier", lambda: "s2-pro")
    assert el.enabled_names("FishAudio_Enabled_Models") == wanted


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


def test_comfyui_vendor_switch_gates_comfyui_and_rh_app(monkeypatch):
    import RH_ComfyUI.utils.backends.rh_app.config as rcfg
    import RH_ComfyUI.utils.backends.comfyui.config as ccfg
    from RH_ComfyUI.models.bridge import AdapterChannel
    from gsuid_core.utils.plugins_config.models import GsBoolConfig
    from RH_ComfyUI.utils.backends.rh_app.executor import RHAppAdapter
    from RH_ComfyUI.utils.backends.comfyui.executor import ComfyUIAdapter

    toggle = SERVICE_CONFIG_DEFAULT["ComfyUI_Enable"]
    assert isinstance(toggle, GsBoolConfig)
    assert toggle.data is True

    def _off(key: str):
        if key == "ComfyUI_Enable":
            return False
        if key == "ComfyUI_Enabled_Workflows":
            return ["qwen_2512"]
        if key == "RH_App_Enabled_Apps":
            return ["anima"]
        return None

    monkeypatch.setattr(ccfg, "_cfg", _off)
    monkeypatch.setattr(rcfg, "_cfg", _off)

    assert ccfg.is_comfyui_vendor_enabled() is False
    assert ccfg.is_comfyui_workflow_enabled("qwen_2512") is False
    assert rcfg.is_rh_app_vendor_enabled() is False
    assert rcfg.is_rh_app_enabled("anima") is False

    ch_cf = AdapterChannel("comfyui", model_name="qwen_2512", workflow_file="qwen_2512.json")
    ch_rh = AdapterChannel("rh_app", model_name="anima", workflow_file="123")
    assert asyncio.run(ch_cf.check_available()) is False
    assert "启用 ComfyUI / RunningHub 供应商" in asyncio.run(ch_cf.unavailable_reason())
    assert asyncio.run(ch_rh.check_available()) is False
    assert "启用 ComfyUI / RunningHub 供应商" in asyncio.run(ch_rh.unavailable_reason())

    assert asyncio.run(ComfyUIAdapter().check_available()) is False
    assert "启用 ComfyUI / RunningHub 供应商" in asyncio.run(ComfyUIAdapter().get_unavailable_reason())
    assert asyncio.run(RHAppAdapter().check_available()) is False
    assert "启用 ComfyUI / RunningHub 供应商" in asyncio.run(RHAppAdapter().get_unavailable_reason())
