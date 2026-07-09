"""OpenAI 兼容供应商池 — 尺寸映射 / 图片解析 / 通道可用性 / 配置解析 + 绑定注入"""

import io
import base64
import asyncio

from PIL import Image

import RH_ComfyUI.utils.backends.openai_image.api as oapi
import RH_ComfyUI.utils.backends.openai_image.providers as oprov
from RH_ComfyUI.core import channel_registry
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsBoolConfig, GsRepeatGroupConfig
from RH_ComfyUI.utils.backends.openai_image import channel as ochan
from RH_ComfyUI.utils.backends.openai_image.channel import OpenAIImageChannel, OpenAIImageCredentials


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_size_mapping():
    assert oapi.size_for("16:9", 720, 1280) == "1792x1024"
    # 无 ratio 时按宽高取最接近枚举
    assert oapi.size_for(None, 1024, 1024) == "1024x1024"
    assert oapi.ratio_from_wh(1920, 1080) == "16:9"


def test_extract_b64_image():
    data = {"data": [{"b64_json": _png_b64()}]}
    out = asyncio.run(oapi._extract_image(data, "qwen-image"))
    assert isinstance(out, bytes) and out[:8] == b"\x89PNG\r\n\x1a\n"


def test_extract_missing_image_raises():
    try:
        asyncio.run(oapi._extract_image({"data": []}, "m"))
    except oapi.OpenAIImageError as e:
        assert "缺少" in str(e) or "data" in str(e)
    else:
        raise AssertionError("应抛 OpenAIImageError")


def test_channel_availability():
    def resolver_ok() -> OpenAIImageCredentials:
        return OpenAIImageCredentials(True, "sk-secret123", "https://qianfan.baidubce.com/v2")

    def resolver_off() -> OpenAIImageCredentials:
        return OpenAIImageCredentials(False, "sk-secret123", "https://x/v2")

    ch = OpenAIImageChannel("baidu", credentials_resolver=resolver_ok)
    assert asyncio.run(ch.check_available()) is True
    assert ch.audit_key_prefix() == "sk-sec"

    ch_off = OpenAIImageChannel("baidu", credentials_resolver=resolver_off)
    assert asyncio.run(ch_off.check_available()) is False


def test_channel_invoke_returns_output(monkeypatch):
    async def _fake_generate(**kwargs) -> bytes:
        assert kwargs["model"] == "qwen-image"
        assert kwargs["base_url"] == "https://qianfan.baidubce.com/v2"
        return b"PNGBYTES"

    monkeypatch.setattr(ochan, "generate_image", _fake_generate)

    ch = OpenAIImageChannel(
        "baidu",
        credentials_resolver=lambda: OpenAIImageCredentials(True, "sk-xyz", "https://qianfan.baidubce.com/v2"),
    )
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="一张海报")
    out = asyncio.run(ch.invoke(request=req, vendor_model="qwen-image"))
    assert out.status == "ok" and out.data == b"PNGBYTES"
    assert out.metadata["channel"] == "baidu"


class _FakeServiceConfig:
    def __init__(self, cfg) -> None:
        self._cfg = cfg

    def get_config(self, key: str):
        return self._cfg


def _providers_config() -> GsRepeatGroupConfig:
    return GsRepeatGroupConfig(
        "供应商", "",
        template={},
        data=[
            {
                "enable": GsBoolConfig("启用", "", True),
                "name": GsStrConfig("名", "", "baidu"),
                "base_url": GsStrConfig("url", "", "https://qianfan.baidubce.com/v2"),
                "api_key": GsStrConfig("key", "", "sk-secret", secret=True),
                "models": GsRepeatGroupConfig(
                    "模型", "", template={},
                    data=[
                        {
                            "model_real_name": GsStrConfig("内部", "", "qwen_2512"),
                            "model_id": GsStrConfig("供应商模型", "", "qwen-image"),
                        }
                    ],
                ),
            }
        ],
    )


def test_resolve_and_sync_binding(monkeypatch):
    channel_registry.clear()
    monkeypatch.setattr(oprov, "SERVICE_CONFIG", _FakeServiceConfig(_providers_config()))

    entries = oprov.resolve_provider_entries()
    assert len(entries) == 1
    assert entries[0].name == "baidu" and entries[0].enable is True
    assert entries[0].models[0].model_real_name == "qwen_2512"
    assert entries[0].models[0].model_id == "qwen-image"

    oprov.sync_openai_image_providers()
    bindings = channel_registry.bindings_for("qwen_2512")
    assert [b.channel.name for b in bindings] == ["baidu"]
    assert bindings[0].vendor_model == "qwen-image"

    # 禁用后重新 sync 应移除绑定
    disabled = _providers_config()
    disabled.data[0]["enable"] = GsBoolConfig("启用", "", False)
    monkeypatch.setattr(oprov, "SERVICE_CONFIG", _FakeServiceConfig(disabled))
    oprov.sync_openai_image_providers()
    assert channel_registry.bindings_for("qwen_2512") == []
    channel_registry.clear()
