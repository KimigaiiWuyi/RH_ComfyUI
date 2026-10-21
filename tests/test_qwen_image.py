"""千问图像:同步请求体、分系列字段、北京/新加坡积分。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.models.image.qwen import QWEN_IMAGE_MODELS
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.rh_config.service_config import _IMAGE_MODEL_REAL_NAMES
from RH_ComfyUI.utils.mappers.qwen_image import (
    call_from_request,
    build_qwen_image_body,
    resolve_qwen_image_size,
)
from RH_ComfyUI.core.telemetry.wire_capture import get_wire_audit, clear_wire_audit
from RH_ComfyUI.utils.backends.dashscope.config import DASHSCOPE_MODEL_OPTIONS
from RH_ComfyUI.utils.mappers.qwen_image_billing import (
    prepaid_tier,
    qwen_image_region,
    settle_qwen_image_points,
    estimate_qwen_image_points,
)
from RH_ComfyUI.utils.mappers.qwen_image_catalog import (
    SIZE_1K,
    SIZE_2K,
    QWEN_IMAGE_20,
    QWEN_IMAGE_30,
    QWEN_IMAGE_NAMES,
    QWEN_IMAGE_SPECS,
    TIER_1K_MAX_AREA,
    QWEN_IMAGE_20_PRO,
    QWEN_IMAGE_21_PRO,
    QWEN_IMAGE_30_PRO,
    QWEN_IMAGE_21_TURBO,
    qwen_image_spec,
)
from RH_ComfyUI.utils.backends.dashscope.image_api import (
    DashScopeImageAPI,
    generation_url,
    qwen_image_error_text,
    image_urls_from_payload,
)

_CN = "https://dashscope.aliyuncs.com/api/v1"
_INTL = "https://dashscope-intl.aliyuncs.com/api/v1"
_PNG = b"\x89PNG\r\n\x1a\nhello"


def _req(
    *,
    prompt: str = "一只猫",
    ratio: str | None = "1:1",
    images: list[bytes] | None = None,
    negative_prompt: str = "",
    seed: int | None = None,
    watermark: bool = False,
) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt=prompt,
        ratio=ratio,
        images=[] if images is None else images,
        negative_prompt=negative_prompt,
        seed=seed,
        watermark=watermark,
    )


def _params(request: GenerationRequest, **params: str | int | bool) -> GenerationRequest:
    for key, value in params.items():
        request.params[key] = value
    return request


def _parameters(body: object) -> dict[str, object]:
    assert isinstance(body, dict)
    raw = body["parameters"]
    assert isinstance(raw, dict)
    out: dict[str, object] = {}
    for key, value in raw.items():
        if isinstance(key, str):
            out[key] = value
    return out


def _area(size: str) -> int:
    width, height = size.split("*")
    return int(width) * int(height)


def test_sizes_stay_inside_official_area() -> None:
    assert set(SIZE_1K) == set(SIZE_2K)
    for size in SIZE_1K.values():
        assert 512 * 512 <= _area(size) <= TIER_1K_MAX_AREA
    for size in SIZE_2K.values():
        assert TIER_1K_MAX_AREA < _area(size) <= 2048 * 2048
    assert SIZE_2K["16:9"] == "2688*1536"
    assert SIZE_2K["21:9"] == "2688*1152"
    assert resolve_qwen_image_size("auto", "2K") is None
    assert resolve_qwen_image_size(None, "1K") is None
    assert resolve_qwen_image_size("", "1K") is None
    assert resolve_qwen_image_size("1:1", None) == "1024*1024"
    assert resolve_qwen_image_size("1:1", "2K") == "2048*2048"
    assert resolve_qwen_image_size("weird", "1K") is None


def test_options_append_image_names_after_tts() -> None:
    assert DASHSCOPE_MODEL_OPTIONS[5:] == QWEN_IMAGE_NAMES
    for name in QWEN_IMAGE_NAMES:
        assert name not in _IMAGE_MODEL_REAL_NAMES


def test_series_body_fields() -> None:
    pro = qwen_image_spec(QWEN_IMAGE_30_PRO)
    turbo = qwen_image_spec(QWEN_IMAGE_21_TURBO)
    older = qwen_image_spec(QWEN_IMAGE_20)
    assert pro is not None and turbo is not None and older is not None
    body = build_qwen_image_body(
        call_from_request(
            pro,
            _params(
                _req(negative_prompt="模糊", seed=0),
                prompt_extend_mode="direct",
                enable_thinking=False,
                n="2",
            ),
        ),
        [],
    )
    assert body["model"] == QWEN_IMAGE_30_PRO
    content = body["input"]["messages"][0]["content"]
    assert content == [{"text": "一只猫"}]
    params = _parameters(body)
    assert params["n"] == 2
    assert params["seed"] == 0
    assert params["size"] == "1024*1024"
    assert params["negative_prompt"] == "模糊"
    assert params["prompt_extend"] is True
    assert params["prompt_extend_mode"] == "direct"
    assert params["enable_thinking"] is False
    assert params["watermark"] is False

    edited = build_qwen_image_body(
        call_from_request(pro, _params(_req(images=[_PNG, _PNG]), prompt_extend=False)),
        ["https://example.test/a.png", "https://example.test/b.png"],
    )
    edited_content = edited["input"]["messages"][0]["content"]
    assert edited_content[0] == {"image": "https://example.test/a.png"}
    assert edited_content[1] == {"image": "https://example.test/b.png"}
    assert edited_content[2] == {"text": "一只猫"}
    closed = _parameters(edited)
    assert "prompt_extend_mode" not in closed
    assert "enable_thinking" not in closed
    assert "negative_prompt" not in closed

    turbo_body = build_qwen_image_body(call_from_request(turbo, _req(ratio="auto")), [])
    turbo_params = _parameters(turbo_body)
    assert "negative_prompt" not in turbo_params
    assert "size" not in turbo_params
    assert turbo_params["prompt_extend_mode"] == "direct"
    assert turbo_params["enable_thinking"] is True

    older_body = build_qwen_image_body(call_from_request(older, _req(negative_prompt="低画质")), [])
    older_params = _parameters(older_body)
    assert older_params["negative_prompt"] == "低画质"
    assert "prompt_extend_mode" not in older_params
    assert "enable_thinking" not in older_params


def test_validate_rejects_illegal_combinations() -> None:
    turbo = QWEN_IMAGE_MODELS[2]()
    assert turbo.name == QWEN_IMAGE_21_PRO
    with pytest.raises(ValidationError, match="反向提示词"):
        turbo.validate(_req(negative_prompt="模糊"))
    with pytest.raises(ValidationError, match="agent"):
        turbo.validate(_params(_req(images=[_PNG]), prompt_extend_mode="agent"))
    older = QWEN_IMAGE_MODELS[5]()
    assert older.name == QWEN_IMAGE_20
    with pytest.raises(ValidationError, match="改写方式"):
        older.validate(_params(_req(), prompt_extend_mode="direct"))
    with pytest.raises(ValidationError, match="10MB"):
        older.validate(_req(images=[b"x" * (10 * 1024 * 1024 + 1)]))
    with pytest.raises(ValidationError, match="空的"):
        older.validate(_req(images=[b""]))
    pro = QWEN_IMAGE_MODELS[0]()
    request = _params(_req(), n="3")
    pro.validate(request)
    assert request.params["n"] == 3


def test_beijing_and_singapore_points() -> None:
    assert estimate_qwen_image_points(QWEN_IMAGE_30_PRO, image_size="1K", ratio="1:1", base_url=_CN) == 25
    assert estimate_qwen_image_points(QWEN_IMAGE_30_PRO, image_size="2K", ratio="1:1", base_url=_CN) == 50
    assert (
        estimate_qwen_image_points(
            QWEN_IMAGE_30_PRO,
            image_size="1K",
            ratio="1:1",
            num_input_images=2,
            base_url=_CN,
        )
        == 29
    )
    assert estimate_qwen_image_points(QWEN_IMAGE_30, image_size="2K", ratio="16:9", base_url=_CN) == 18
    assert estimate_qwen_image_points(QWEN_IMAGE_21_PRO, n=2, base_url=_CN) == 50
    assert estimate_qwen_image_points(QWEN_IMAGE_21_TURBO, base_url=_CN) == 10
    assert estimate_qwen_image_points(QWEN_IMAGE_20_PRO, base_url=_CN) == 50
    assert estimate_qwen_image_points(QWEN_IMAGE_20, base_url=_CN) == 20
    assert estimate_qwen_image_points(QWEN_IMAGE_30_PRO, image_size="1K", ratio="1:1", base_url=_INTL) == 30
    assert estimate_qwen_image_points(QWEN_IMAGE_30_PRO, image_size="2K", ratio="1:1", base_url=_INTL) == 57
    assert (
        estimate_qwen_image_points(
            QWEN_IMAGE_30_PRO,
            image_size="1K",
            ratio="1:1",
            num_input_images=1,
            base_url=_INTL,
        )
        == 33
    )
    assert estimate_qwen_image_points(QWEN_IMAGE_30, image_size="2K", ratio="1:1", base_url=_INTL) == 23
    assert estimate_qwen_image_points(QWEN_IMAGE_21_PRO, base_url=_INTL) == 29
    assert estimate_qwen_image_points(QWEN_IMAGE_21_TURBO, base_url=_INTL) == 12
    assert estimate_qwen_image_points(QWEN_IMAGE_20_PRO, base_url=_INTL) == 56
    assert estimate_qwen_image_points(QWEN_IMAGE_20, base_url=_INTL) == 26
    assert prepaid_tier(QWEN_IMAGE_30_PRO, "1K", "auto") == "2k"
    assert prepaid_tier(QWEN_IMAGE_30_PRO, "1K", None) == "2k"
    assert prepaid_tier(QWEN_IMAGE_30_PRO, "1K", "") == "2k"
    assert prepaid_tier(QWEN_IMAGE_30, "1K", "auto") == "1k"
    assert estimate_qwen_image_points(QWEN_IMAGE_30_PRO, ratio="auto", base_url=_CN) == 50
    assert qwen_image_region("https://ws.ap-southeast-1.maas.aliyuncs.com/api/v1") == "intl"
    assert qwen_image_region(_CN) == "cn"


def test_settle_uses_usage_or_keeps_prepaid() -> None:
    assert (
        settle_qwen_image_points(
            QWEN_IMAGE_30_PRO,
            {"output_width": 2048, "output_height": 2048, "output_image_count": 1},
            base_url=_CN,
        )
        == 50
    )
    assert (
        settle_qwen_image_points(
            QWEN_IMAGE_30_PRO,
            {"output_image_type": "qima_output_1k", "output_image_count": 1},
            base_url=_CN,
        )
        == 25
    )
    assert settle_qwen_image_points(QWEN_IMAGE_30_PRO, {"output_image_count": 1}, base_url=_CN) is None
    assert settle_qwen_image_points(QWEN_IMAGE_30, {"image_count": 1}, base_url=_CN) == 18
    assert (
        settle_qwen_image_points(
            QWEN_IMAGE_21_PRO,
            {"width": 1024, "height": 1024, "image_count": 2},
            base_url=_CN,
        )
        == 50
    )
    overlaid = settle_qwen_image_points(
        QWEN_IMAGE_30_PRO,
        {
            "raw_task": {"usage": {"output_width": 1024, "output_height": 1024, "output_image_count": 1}},
            "output_image_count": 2,
        },
        base_url=_CN,
    )
    assert overlaid == 50
    assert settle_qwen_image_points(QWEN_IMAGE_30_PRO, {}, base_url=_CN) is None


def test_point_range_is_open() -> None:
    for ctor in QWEN_IMAGE_MODELS:
        model = ctor()
        low, high = model.point_range()
        assert low < high
        assert low == model.point_cost
    # 同一档按「单价 × 张数」一次向上取整,不是先取整再乘。
    assert QWEN_IMAGE_MODELS[0]().point_range() == (25, 345)
    assert QWEN_IMAGE_MODELS[1]().point_range() == (18, 142)
    assert QWEN_IMAGE_MODELS[3]().point_range() == (10, 69)


def test_parse_urls_and_vendor_error() -> None:
    payload = {
        "output": {
            "choices": [
                {"message": {"content": [{"image": " https://a "}, {"text": "x"}, {"image": "https://b"}]}},
                {"message": {"content": [{"image": "https://c"}]}},
            ]
        }
    }
    assert image_urls_from_payload(payload) == ["https://a", "https://b", "https://c"]
    assert image_urls_from_payload({"code": "InvalidParameter"}) == []
    text = qwen_image_error_text(400, {"code": "InvalidParameter", "message": "n must be 1", "request_id": "r1"})
    assert "InvalidParameter" in text
    assert "n must be 1" in text
    assert "r1" in text


def test_generate_is_sync_and_keeps_every_image(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg
    import RH_ComfyUI.utils.backends.dashscope.image_api as image_api

    def _cfg(key: str) -> object:
        if key == "HappyHorse_Enable_dashscope":
            return True
        if key == "HappyHorse_apikey_dashscope":
            return "sk-test-key-1234567890"
        if key == "HappyHorse_BaseURL_dashscope":
            return _CN + "/"
        return None

    seen: dict[str, object] = {}

    async def _post(url: str, headers: dict[str, str], body: object) -> httpx.Response:
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = body
        audit = get_wire_audit()
        seen["audit_prompt"] = audit["prompt"] if "prompt" in audit else None
        seen["audit_request"] = audit["request"] if "request" in audit else None
        return httpx.Response(
            200,
            json={
                "request_id": "req-1",
                "output": {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"image": "https://cdn.test/1.png"},
                                    {"image": "https://cdn.test/2.png"},
                                ]
                            }
                        }
                    ]
                },
                "usage": {"image_count": 2, "width": 1024, "height": 1024},
            },
        )

    async def _download(url: str, **kwargs: object) -> bytes:
        assert kwargs["label"] == "qwen-image"
        return _PNG + url.encode("ascii")

    monkeypatch.setattr(dcfg, "_cfg", _cfg)
    monkeypatch.setattr(image_api, "download_with_network_retry", _download)
    clear_wire_audit()
    spec = qwen_image_spec(QWEN_IMAGE_21_TURBO)
    assert spec is not None
    from RH_ComfyUI.utils.mappers.qwen_image import qwen_image_mapper

    out = asyncio.run(qwen_image_mapper(_req(), DashScopeImageAPI(post=_post), spec=spec))
    assert seen["url"] == generation_url(_CN)
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer sk-test-key-1234567890"
    assert "X-DashScope-Async" not in headers
    assert out.data.startswith(_PNG)
    extra = out.outputs["image_2"]
    assert isinstance(extra, bytes)
    assert extra.endswith(b"2.png")
    assert "image" not in out.outputs
    assert out.usage["image_count"] == 2
    assert out.usage["request_id"] == "req-1"
    assert seen["audit_prompt"] == "一只猫"
    request = seen["audit_request"]
    assert isinstance(request, dict)
    assert request["model"] == QWEN_IMAGE_21_TURBO


def test_vendor_error_and_empty_key_do_not_post(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg

    calls = {"n": 0}

    async def _post(url: str, headers: dict[str, str], body: object) -> httpx.Response:
        del url, headers, body
        calls["n"] += 1
        return httpx.Response(400, json={"code": "InvalidParameter", "message": "bad size", "request_id": "r2"})

    def _cfg(key: str) -> object:
        if key == "HappyHorse_Enable_dashscope":
            return True
        if key == "HappyHorse_apikey_dashscope":
            return "sk-test-key-1234567890"
        if key == "HappyHorse_BaseURL_dashscope":
            return _INTL
        return None

    monkeypatch.setattr(dcfg, "_cfg", _cfg)
    api = DashScopeImageAPI(post=_post)
    with pytest.raises(RuntimeError, match="InvalidParameter"):
        asyncio.run(api.generate({"model": QWEN_IMAGE_20}, prompt="一只猫"))
    assert calls["n"] == 1

    def _empty(key: str) -> object:
        if key == "HappyHorse_Enable_dashscope":
            return True
        if key == "HappyHorse_apikey_dashscope":
            return "  "
        return _CN

    monkeypatch.setattr(dcfg, "_cfg", _empty)
    with pytest.raises(RuntimeError, match="API Key"):
        asyncio.run(api.generate({"model": QWEN_IMAGE_20}, prompt="一只猫"))
    assert calls["n"] == 1


def test_old_list_leaves_image_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import RH_ComfyUI.utils.backends.enabled_list as el
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg

    saved = ["happyhorse1.1", "wan3.0", "qwen-audio-3.0-tts-flash"]
    monkeypatch.setattr(dcfg, "_cfg", lambda key: saved if key == "DashScope_Enabled_Models" else True)
    monkeypatch.setattr(el, "_cfg", lambda key: saved if key == "DashScope_Enabled_Models" else True)
    model = QWEN_IMAGE_MODELS[0]()
    assert asyncio.run(model.check_available()) is False
    reason = asyncio.run(model.unavailable_reason())
    assert QWEN_IMAGE_30_PRO in reason
    gate = AdapterChannel("dashscope_image", model_name=QWEN_IMAGE_30_PRO)._enable_gate()
    assert gate is not None
    assert QWEN_IMAGE_30_PRO in gate
    assert AdapterChannel("dashscope_image").supports_remote_cancel() is False


def test_catalog_priorities_and_flags() -> None:
    by_name = {spec.name: spec for spec in QWEN_IMAGE_SPECS}
    assert by_name[QWEN_IMAGE_30_PRO].priority == 69
    assert by_name[QWEN_IMAGE_30].priority == 68
    assert by_name[QWEN_IMAGE_20_PRO].priority == 65
    assert by_name[QWEN_IMAGE_20].priority == 63
    assert by_name[QWEN_IMAGE_21_PRO].max_images == 10
    assert by_name[QWEN_IMAGE_21_PRO].allow_negative is False
    assert by_name[QWEN_IMAGE_20].allow_extend_mode is False
    assert by_name[QWEN_IMAGE_30].allow_negative is True
