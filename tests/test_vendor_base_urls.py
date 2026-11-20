"""官方供应商 Base URL 配置默认值与反代热读单测(全离线)。"""

from __future__ import annotations

import pytest

from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsStrConfig


class _FakeItem:
    def __init__(self, data: object) -> None:
        self.data = data


class _FakeConfig:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._m = mapping

    def get_config(self, key: str) -> _FakeItem:
        return _FakeItem(self._m[key] if key in self._m else "")


def test_official_vendor_base_url_defaults() -> None:
    expected: dict[str, str] = {
        "ComfyUI_BaseURL": "127.0.0.1:8188",
        "RH_BaseURL": "https://www.runninghub.cn",
        "Gemini_Image_BaseURL": "https://generativelanguage.googleapis.com",
        "MiniMax_BaseURL": "https://api.minimaxi.com",
        "MIMO_BaseURL": "https://api.xiaomimimo.com/v1",
        "FishAudio_BaseURL": "https://api.fish.audio",
        "Seedance_BaseURL_ark": "https://ark.cn-beijing.volces.com/api/v3",
        "Seedance_BaseURL_runninghub": "https://www.runninghub.cn",
        "Azure_Image_Endpoint": "",
        "Kling_Endpoint": "https://api-beijing.klingai.com",
        "FAL_Endpoint": "https://queue.fal.run",
        "HappyHorse_BaseURL_dashscope": "https://dashscope.aliyuncs.com/api/v1",
        "TX_AIArt_BaseURL": "https://aiart.tencentcloudapi.com",
    }
    for key, default_val in expected.items():
        item = SERVICE_CONFIG_DEFAULT[key]
        assert isinstance(item, GsStrConfig), key
        assert item.data == default_val, key


def test_minimax_base_url_hot_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.minimax.config as mcfg
    from RH_ComfyUI.utils.backends.minimax.api import MiniMaxAPI
    from RH_ComfyUI.utils.backends.minimax.h3_channel import MiniMaxH3Channel
    from RH_ComfyUI.utils.backends.minimax.h3_image_channel import MiniMaxH3ImageChannel

    state: dict[str, object] = {"MiniMax_BaseURL": "", "MiniMax_apikey": "k1"}
    monkeypatch.setattr(mcfg, "_cfg", lambda key: state[key] if key in state else "")

    api = MiniMaxAPI()
    assert api.base_url == "https://api.minimaxi.com"
    assert api.generation_url == "https://api.minimaxi.com/v1/image_generation"
    assert api.t2a_async_url == "https://api.minimaxi.com/v1/t2a_async_v2"
    assert api.t2a_query_url == "https://api.minimaxi.com/v1/query/t2a_async_query_v2"
    assert api.file_retrieve_url == "https://api.minimaxi.com/v1/files/retrieve"
    assert api.file_upload_url == "https://api.minimaxi.com/v1/files/upload"
    assert api.voice_clone_url == "https://api.minimaxi.com/v1/voice_clone"

    h3_ch = MiniMaxH3Channel()
    h3_img_ch = MiniMaxH3ImageChannel()
    p1 = h3_ch._get_provider()
    p2 = h3_img_ch._get_provider()
    assert p1.base_url == "https://api.minimaxi.com"
    assert p2.base_url == "https://api.minimaxi.com"

    state["MiniMax_BaseURL"] = "https://proxy.example.com/minimax/"
    assert api.base_url == "https://proxy.example.com/minimax"
    assert api.generation_url == "https://proxy.example.com/minimax/v1/image_generation"
    assert api.voice_clone_url == "https://proxy.example.com/minimax/v1/voice_clone"
    assert h3_ch._get_provider().base_url == "https://proxy.example.com/minimax"
    assert h3_img_ch._get_provider().base_url == "https://proxy.example.com/minimax"

    # 兼容旧 2-tuple credentials_resolver
    compat = MiniMaxH3Channel(credentials_resolver=lambda: ("k2", False))
    assert compat._get_provider().base_url == "https://proxy.example.com/minimax"


def test_mimo_and_fishaudio_base_url_hot_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.mimo.api as mapi
    import RH_ComfyUI.utils.backends.fishaudio.api as fapi

    cfg: dict[str, object] = {}
    monkeypatch.setattr(mapi, "SERVICE_CONFIG", _FakeConfig(cfg))
    monkeypatch.setattr(fapi, "SERVICE_CONFIG", _FakeConfig(cfg))

    mimo = mapi.MIMOAPI()
    fish = fapi.FishAudioAPI()
    assert mimo.base_url == "https://api.xiaomimimo.com/v1"
    assert mimo.chat_url == "https://api.xiaomimimo.com/v1/chat/completions"
    assert fish.base_url == "https://api.fish.audio"

    cfg["MIMO_BaseURL"] = "https://proxy.example.com/mimo/v1/"
    cfg["FishAudio_BaseURL"] = "https://proxy.example.com/fish/"
    assert mimo.base_url == "https://proxy.example.com/mimo/v1"
    assert mimo.chat_url == "https://proxy.example.com/mimo/v1/chat/completions"
    assert fish.base_url == "https://proxy.example.com/fish"


def test_tx_aiart_base_url_and_proxy_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.tx_aiart.api as tapi

    cfg: dict[str, object] = {}
    monkeypatch.setattr(tapi, "SERVICE_CONFIG", _FakeConfig(cfg))
    api = tapi.TxAiartAPI()
    assert api.base_url == "https://aiart.tencentcloudapi.com"

    signed = tapi.sign_tc3(
        secret_id="AKID",
        secret_key="SKEY",
        service=tapi.SERVICE,
        host=api.host,
        action=tapi.ACTION,
        payload="{}",
        timestamp=1700000000,
        region=tapi.DEFAULT_REGION,
        version=tapi.VERSION,
    )
    direct_headers = tapi._request_headers(api.base_url, signed, api.host)
    assert direct_headers["Host"] == "aiart.tencentcloudapi.com"

    cfg["TX_AIArt_BaseURL"] = "https://proxy.example.com/tx-aiart/"
    assert api.base_url == "https://proxy.example.com/tx-aiart"
    proxy_headers = tapi._request_headers(api.base_url, signed, api.host)
    assert "Host" not in proxy_headers
    assert proxy_headers["Authorization"] == signed["Authorization"]


def test_runninghub_comfyui_and_seedance_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.rh_app.api as rapi
    import RH_ComfyUI.utils.backends.comfyui.api as capi
    from RH_ComfyUI.utils.backends.seedance.providers.runninghub import _view_url

    cfg: dict[str, object] = {
        "ComfyUI_BaseURL": "使用RunningHub代理",
        "RH_apikey": "rh-key",
        "RH_BaseURL": "",
    }
    monkeypatch.setattr(rapi, "SERVICE_CONFIG", _FakeConfig(cfg))
    monkeypatch.setattr(capi, "SERVICE_CONFIG", _FakeConfig(cfg))

    rh_api = rapi.RHAppAPI()
    cf_api = capi.ComfyUIAPI()
    assert rh_api.base_url == "https://www.runninghub.cn"
    assert cf_api.url == "https://www.runninghub.cn/proxy/rh-key"
    assert cf_api.server_address == "www.runninghub.cn/proxy/rh-key"
    assert cf_api._rh_headers("rh-key")["Host"] == "www.runninghub.cn"
    assert _view_url("a.png") == "https://www.runninghub.cn/view?filename=a.png"

    cfg["RH_BaseURL"] = "https://proxy.example.com/rh/"
    assert rh_api.base_url == "https://proxy.example.com/rh"
    assert cf_api.url == "https://proxy.example.com/rh/proxy/rh-key"
    assert cf_api.server_address == "proxy.example.com/rh/proxy/rh-key"
    assert "Host" not in cf_api._rh_headers("rh-key")
    assert _view_url("a.png", "https://proxy.example.com/rh/") == "https://proxy.example.com/rh/view?filename=a.png"


def test_gemini_default_base_url_and_custom_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.gemini_image.api as gapi

    cfg: dict[str, object] = {
        "Gemini_Image_apikey": "AIzaKEY",
        "Gemini_Image_BaseURL": "https://generativelanguage.googleapis.com",
    }
    monkeypatch.setattr(gapi, "SERVICE_CONFIG", _FakeConfig(cfg))
    api = gapi.GeminiImageAPI()
    assert api.base_url == "https://generativelanguage.googleapis.com"
    assert api.custom_base_url == ""

    client_default = api._build_client()
    assert "generativelanguage" in client_default._api_client._http_options.base_url

    cfg["Gemini_Image_BaseURL"] = ""
    assert api.base_url == "https://generativelanguage.googleapis.com"
    assert api.custom_base_url == ""

    cfg["Gemini_Image_BaseURL"] = "https://proxy.example.com/gemini/"
    assert api.custom_base_url == "https://proxy.example.com/gemini/"
    client_proxy = api._build_client()
    assert client_proxy._api_client._http_options.base_url == "https://proxy.example.com/gemini/"


def test_scheme_normalization_when_user_omits_https(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.mimo.api as mapi
    import RH_ComfyUI.utils.backends.rh_app.api as rapi
    import RH_ComfyUI.utils.backends.comfyui.api as capi
    import RH_ComfyUI.utils.backends.tx_aiart.api as tapi
    import RH_ComfyUI.utils.backends.fishaudio.api as fapi
    import RH_ComfyUI.utils.backends.minimax.config as mcfg
    import RH_ComfyUI.utils.backends.gemini_image.api as gapi
    from RH_ComfyUI.utils.backends.minimax.api import MiniMaxAPI

    cfg: dict[str, object] = {
        "MiniMax_BaseURL": "proxy.example.com/minimax/",
        "MIMO_BaseURL": "proxy.example.com/mimo/v1/",
        "FishAudio_BaseURL": "proxy.example.com/fish/",
        "TX_AIArt_BaseURL": "proxy.example.com/tx/",
        "RH_BaseURL": "proxy.example.com/rh/",
        "ComfyUI_BaseURL": "使用RunningHub代理",
        "RH_apikey": "rh-key",
        "Gemini_Image_BaseURL": "proxy.example.com/gemini/",
    }
    monkeypatch.setattr(mcfg, "_cfg", lambda key: cfg[key] if key in cfg else "")
    for mod in (mapi, fapi, tapi, rapi, capi, gapi):
        monkeypatch.setattr(mod, "SERVICE_CONFIG", _FakeConfig(cfg))

    assert MiniMaxAPI().base_url == "https://proxy.example.com/minimax"
    assert mapi.MIMOAPI().base_url == "https://proxy.example.com/mimo/v1"
    assert fapi.FishAudioAPI().base_url == "https://proxy.example.com/fish"
    assert tapi.TxAiartAPI().base_url == "https://proxy.example.com/tx"
    assert rapi.RHAppAPI().base_url == "https://proxy.example.com/rh"
    assert capi.ComfyUIAPI().url == "https://proxy.example.com/rh/proxy/rh-key"
    assert gapi.GeminiImageAPI().base_url == "https://proxy.example.com/gemini/"
    assert gapi.GeminiImageAPI().custom_base_url == "https://proxy.example.com/gemini/"
