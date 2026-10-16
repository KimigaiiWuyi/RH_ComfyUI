"""Azure 生图:编辑走 /images/edits multipart,generations 不带 response_format。"""

import io
import base64
import asyncio
from typing import Any

import pytest
from PIL import Image

import RH_ComfyUI.utils.backends.azure_image.api as azure_api
from RH_ComfyUI.core import ChannelError
from RH_ComfyUI.utils.backends import azure_image
from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsListStrConfig
from RH_ComfyUI.core.telemetry.wire_capture import get_wire_audit, clear_wire_audit
from RH_ComfyUI.utils.backends.azure_image.api import AzureGPTImage2API, azure_http_error, wire_api_version
from RH_ComfyUI.utils.backends.azure_image.catalog import (
    AZURE_IMAGE_MODEL_NAMES,
    AZURE_IMAGE_DEFAULT_MODELS,
)
from RH_ComfyUI.utils.backends.azure_image.channel import AzureRuntime, AzureImageChannel


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (1, 2, 3)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _fake_cfg(**overrides: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "Azure_Image_apikey": "azkey-123456",
        "Azure_Image_Endpoint": "https://res.openai.azure.com",
        "Azure_Image_Deployment": list(AZURE_IMAGE_DEFAULT_MODELS),
        "Azure_Image_ApiVersion": "2025-04-01-preview",
        "Azure_Image_Enable": True,
        "Azure_Image_Max_Concurrency": "2",
    }
    cfg.update(overrides)
    return cfg


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    cfg = _fake_cfg(**overrides)
    monkeypatch.setattr(azure_api, "_cfg", lambda key: cfg[key])


def test_azure_audit_key_prefix_is_head_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    from RH_ComfyUI.utils.audit_key import mask_api_key

    _patch_cfg(monkeypatch)
    assert azure_api.audit_key_prefix() == mask_api_key("azkey-123456")


def test_azure_edit_goes_to_edits_multipart(monkeypatch: pytest.MonkeyPatch) -> None:
    # 带参考图必须走 /images/edits multipart,不能塞进 generations 的 image 字段。
    _patch_cfg(monkeypatch)
    api = AzureGPTImage2API()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_request(method, url, headers=None, json=None, data=None, max_retries=3):
        calls.append((url, {"json": json, "data": data}))
        return {"data": [{"b64_json": _png_b64()}]}

    monkeypatch.setattr(api, "_request", _fake_request)
    out = asyncio.run(api.draw_image("gpt-image-2", "编辑这张图", "9:16", image_list=[b"rawpng"]))
    assert isinstance(out, Image.Image)

    url, kwargs = calls[0]
    assert "/openai/v1/images/edits?" in url
    assert "api-version=preview" in url
    assert "/openai/deployments/" not in url
    assert kwargs["data"] is not None and kwargs["json"] is None
    form = kwargs["data"]
    dumped = str(form._fields)
    assert "name': 'model'" in dumped or 'name\': "model"' in dumped or "'model'" in dumped
    assert "gpt-image-2" in dumped


def test_azure_generation_body_has_model_and_no_response_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    api = AzureGPTImage2API()
    calls: list[tuple[str, dict[str, Any]]] = []
    recorded: dict[str, object] = {}

    async def _fake_request(method, url, headers=None, json=None, data=None, max_retries=3):
        calls.append((url, {"json": json}))
        # 与 draw 同一协程里读。asyncio.run 返回后 ContextVar 不会留在外层。
        recorded.update(get_wire_audit())
        return {"data": [{"b64_json": _png_b64()}]}

    monkeypatch.setattr(api, "_request", _fake_request)
    clear_wire_audit()
    out = asyncio.run(api.draw_image("gpt-image-2", "一张海报", "1:1"))
    assert isinstance(out, Image.Image)

    url, kwargs = calls[0]
    assert "/openai/v1/images/generations?" in url
    assert "api-version=preview" in url
    assert "/openai/deployments/" not in url
    body = kwargs["json"]
    assert body == {
        "model": "gpt-image-2",
        "prompt": "一张海报",
        "n": 1,
        "size": "2560x2560",
        "quality": "medium",
        "background": "auto",
        "output_format": "png",
    }
    assert "response_format" not in body
    request = recorded["request"] if "request" in recorded else None
    assert isinstance(request, dict)
    assert request["model"] == "gpt-image-2"
    assert "response_format" not in request


def test_azure_25_flare_puts_model_in_v1_body_and_forwards_xhigh(monkeypatch: pytest.MonkeyPatch) -> None:
    """2.5 走 v1:body.model=模型 id;xhigh/max 原样进 body。"""
    _patch_cfg(monkeypatch)
    api = AzureGPTImage2API()
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_request(method, url, headers=None, json=None, data=None, max_retries=3):
        calls.append((url, {"json": json}))
        return {"data": [{"b64_json": _png_b64()}]}

    monkeypatch.setattr(api, "_request", _fake_request)
    out = asyncio.run(
        api.draw_image("gpt-image-2.5-flare", "一张海报", "1:1", quality="xhigh"),
    )
    assert isinstance(out, Image.Image)

    url, kwargs = calls[0]
    assert "/openai/v1/images/generations?" in url
    assert kwargs["json"]["model"] == "gpt-image-2.5-flare"
    assert kwargs["json"]["quality"] == "xhigh"


def test_azure_25_sunburst_edit_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    api = AzureGPTImage2API()
    calls: list[str] = []

    async def _fake_request(method, url, headers=None, json=None, data=None, max_retries=3):
        calls.append(url)
        return {"data": [{"b64_json": _png_b64()}]}

    monkeypatch.setattr(api, "_request", _fake_request)
    out = asyncio.run(
        api.draw_image("gpt-image-2.5-sunburst", "编辑", "1:1", image_list=[b"rawpng"]),
    )
    assert isinstance(out, Image.Image)
    assert "/openai/v1/images/edits?" in calls[0]
    assert "/openai/deployments/" not in calls[0]


def test_wire_api_version_rewrites_legacy_date() -> None:
    assert wire_api_version("2025-04-01-preview") == "preview"
    assert wire_api_version("") == "preview"
    assert wire_api_version("preview") == "preview"
    assert wire_api_version("v1") == "v1"


def test_azure_http_error_keeps_deployment_not_found() -> None:
    err = azure_http_error(
        404,
        '{"error":{"code":"DeploymentNotFound","message":"The API deployment for this resource does not exist."}}',
    )
    assert isinstance(err, ChannelError)
    assert err.code == "DeploymentNotFound"
    assert err.retryable is True
    assert err.transient is False
    assert "GPT-Image2" not in str(err)
    assert "找不到该部署" in (err.user_message or "")


def test_azure_404_is_not_retried_or_swallowed_as_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    api = AzureGPTImage2API()
    hits = {"n": 0}

    async def _fake_base(method, url, headers=None, json=None, data=None):
        hits["n"] += 1
        raise azure_http_error(
            404,
            '{"error":{"code":"DeploymentNotFound","message":"The API deployment for this resource does not exist."}}',
        )

    monkeypatch.setattr(api, "_base_request", _fake_base)
    with pytest.raises(ChannelError) as ei:
        asyncio.run(api.draw_image("gpt-image-2.5-flare", "一张海报", "1:1"))
    assert hits["n"] == 1
    assert ei.value.code == "DeploymentNotFound"
    assert "500" not in str(ei.value)
    assert "GPT-Image2" not in str(ei.value)


def test_azure_config_deployment_is_list_defaulting_to_25() -> None:
    cfg = SERVICE_CONFIG_DEFAULT["Azure_Image_Deployment"]
    assert isinstance(cfg, GsListStrConfig)
    assert list(cfg.data) == list(AZURE_IMAGE_DEFAULT_MODELS)
    assert list(cfg.options) == list(AZURE_IMAGE_MODEL_NAMES)
    assert "gpt-image-2.5-sunburst" in cfg.data
    assert "gpt-image-2.5-flare" in cfg.data
    assert "gpt-image-2" not in cfg.data
    assert "gpt-image-2" in cfg.options


def test_azure_channel_gates_unselected_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(
        monkeypatch,
        Azure_Image_Deployment=["gpt-image-2.5-sunburst", "gpt-image-2.5-flare"],
    )
    runtime = AzureRuntime()
    sun = AzureImageChannel(model_name="gpt-image-2.5-sunburst", runtime=runtime)
    flare = AzureImageChannel(model_name="gpt-image-2.5-flare", runtime=runtime)
    gpt2 = AzureImageChannel(model_name="gpt-image-2", runtime=runtime)
    assert asyncio.run(sun.check_available()) is True
    assert asyncio.run(flare.check_available()) is True
    assert asyncio.run(gpt2.check_available()) is False
    assert "未勾选" in asyncio.run(gpt2.unavailable_reason())


def test_azure_channel_shared_inflight_across_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(
        monkeypatch,
        Azure_Image_Deployment=list(AZURE_IMAGE_MODEL_NAMES),
        Azure_Image_Max_Concurrency="1",
    )
    runtime = AzureRuntime()
    sun = AzureImageChannel(model_name="gpt-image-2.5-sunburst", runtime=runtime)
    flare = AzureImageChannel(model_name="gpt-image-2.5-flare", runtime=runtime)
    runtime.inflight = 1
    assert asyncio.run(sun.check_available()) is False
    assert asyncio.run(flare.check_available()) is False
    runtime.inflight = 0
    assert asyncio.run(sun.check_available()) is True
    assert asyncio.run(flare.check_available()) is True


def test_is_deployment_enabled_rejects_legacy_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧配置把 Deployment 写成单个字符串时,不能当已勾选。"""
    _patch_cfg(monkeypatch, Azure_Image_Deployment="gpt-image-dep")
    assert azure_api.is_deployment_enabled("gpt-image-2") is False
    assert azure_api.is_deployment_enabled("gpt-image-2.5-flare") is False


def test_azure_channel_disabled_when_list_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, Azure_Image_Deployment=[])
    ch = AzureImageChannel(model_name="gpt-image-2.5-flare")
    assert asyncio.run(ch.check_available()) is False
    assert "未勾选" in asyncio.run(ch.unavailable_reason())


def test_register_azure_image_binds_family_hosts() -> None:
    import RH_ComfyUI.utils.backends.azure_image.setup as azure_setup
    from RH_ComfyUI.core import channel_registry
    from RH_ComfyUI.utils.backends.azure_image.setup import register_azure_image

    azure_setup._REGISTERED = []
    channel_registry.clear()
    try:
        n = register_azure_image()
        assert n == 3
        for host in AZURE_IMAGE_MODEL_NAMES:
            entries = {binding.channel.name: binding for binding in channel_registry.bindings_for(host)}
            assert "azure" in entries, f"{host} 缺少 Azure 通道"
            assert entries["azure"].vendor_model == host
        register_azure_image()
        for host in AZURE_IMAGE_MODEL_NAMES:
            names = [binding.channel.name for binding in channel_registry.bindings_for(host)]
            assert names.count("azure") == 1
    finally:
        for host in AZURE_IMAGE_MODEL_NAMES:
            channel_registry.unregister(host, "azure")
        azure_setup._REGISTERED = []
        channel_registry.clear()


def test_package_init_does_not_import_client() -> None:
    assert not hasattr(azure_image, "AzureGPTImage2API")
