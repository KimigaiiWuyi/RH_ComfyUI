"""万相 3.0 模型注册 / schema / 校验 / 渲染 / DashScope 启用列表"""

from __future__ import annotations

import asyncio

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.models.video.defs import Wan30Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.wan30_billing import estimate_wan30_points
from RH_ComfyUI.utils.backends.wan30.classify import classify_wan30
from RH_ComfyUI.utils.backends.wan30.provider import Wan30Provider
from RH_ComfyUI.utils.backends.dashscope.config import (
    DASHSCOPE_MODEL_WAN30,
    DASHSCOPE_MODEL_HAPPYHORSE,
    is_dashscope_model_enabled,
)


def test_node_name_and_schema():
    m = Wan30Def()
    assert m.name == "wan3.0"
    schema = m.input_schema()
    assert schema["images"].max_items == 10
    assert schema["video_refs"].max_items == 5
    assert schema["audio_refs"].max_items == 5
    assert "file_url" in schema
    assert "link_url" in schema
    assert schema["frame_mode"].values == ["auto", "first_last", "reference"]
    assert "480p" in (schema["resolution"].values or [])
    assert schema["duration"].minimum == -1
    assert schema["duration"].maximum == 30
    assert schema["generate_audio"].default is True
    assert m.supports_remote_cancel is True


def test_point_range_dynamic():
    m = Wan30Def()
    lo, hi = m.point_range()
    assert lo < hi
    assert lo == estimate_wan30_points("480p", 2)
    assert hi == estimate_wan30_points("1080p", 30)


def test_estimate_cost_reads_resolution_duration():
    m = Wan30Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="跑",
        resolution="720p",
        duration=10,
        params={"resolution": "720p", "duration": 10},
    )
    assert m.estimate_cost(req) == estimate_wan30_points("720p", 10)


def test_validate_accepts_t2v():
    m = Wan30Def()
    m.validate(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="一只猫在草地上奔跑",
            generate_audio=True,
        )
    )


def test_validate_accepts_file_url():
    m = Wan30Def()
    m.validate(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="根据这份手册做广告",
            params={"file_url": "https://ex.com/spec.pdf"},
            generate_audio=True,
        )
    )


def test_validate_rejects_file_and_link():
    m = Wan30Def()
    try:
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="x",
                params={"file_url": "https://ex.com/a.pdf", "link_url": "https://ex.com/b"},
                generate_audio=True,
            )
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "不能同时" in str(e)


def test_validate_rejects_first_last_with_file():
    m = Wan30Def()
    try:
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="x",
                images=[b"a", b"b"],
                params={"frame_mode": "first_last", "file_url": "https://ex.com/a.pdf"},
                generate_audio=True,
            )
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "首帧" in str(e) or "首尾" in str(e)


def test_validate_rejects_first_last_with_video():
    m = Wan30Def()
    try:
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="x",
                images=[b"a", b"b"],
                video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
                params={"frame_mode": "first_last"},
                generate_audio=True,
            )
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "首帧" in str(e) or "首尾" in str(e)


def test_channel_bindings_has_dashscope():
    m = Wan30Def()
    bindings = m.channel_bindings()
    assert bindings
    assert any(b.channel.name == "dashscope" for b in bindings)
    assert any(b.vendor_model == "wan3.0-video" for b in bindings)


def test_render_create_t2v_body():
    async def _run():
        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="一只小猫在月光下的屋顶上奔跑",
            resolution="480p",
            duration=5,
            ratio="adaptive",
            generate_audio=True,
            watermark=False,
        )
        spec = classify_wan30(req)
        p = Wan30Provider(api_key="test")
        method, url, headers, body = await p.render_create(spec, model="wan3.0-video")
        assert method == "POST"
        assert url.endswith("/services/aigc/video-generation/video-synthesis")
        assert headers.get("X-DashScope-Async") == "enable"
        assert body["model"] == "wan3.0-video"
        assert body["input"]["prompt"] == "一只小猫在月光下的屋顶上奔跑"
        assert "media" not in body["input"]
        assert body["parameters"]["resolution"] == "480P"
        assert body["parameters"]["duration"] == 5
        assert body["parameters"]["ratio"] == "adaptive"
        assert body["parameters"]["audio"] is True
        assert body["parameters"]["watermark"] is False

    asyncio.run(_run())


def test_render_create_first_last_media_types():
    async def _run():
        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="从微笑变为大笑",
            images=[b"A", b"B"],
            resolution="720p",
            duration=5,
        )
        spec = classify_wan30(req)
        p = Wan30Provider(api_key="test")
        _m, _u, _h, body = await p.render_create(spec, model="wan3.0-video")
        types = [x["type"] for x in body["input"]["media"]]
        assert types == ["first_frame", "last_frame"]

    asyncio.run(_run())


def test_render_create_file_and_reference_images():
    async def _run():
        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="图片1出现在演示文稿里",
            images=[b"A"],
            resolution="480p",
            duration=10,
            params={"file_url": "https://help.aliyun.com/glass.pptx", "frame_mode": "reference"},
        )
        spec = classify_wan30(req)
        p = Wan30Provider(api_key="test")
        _m, _u, _h, body = await p.render_create(spec, model="wan3.0-video")
        assert body["input"]["prompt"] == "图1出现在演示文稿里"
        types = [x["type"] for x in body["input"]["media"]]
        assert types == ["reference_image", "file"]
        assert body["input"]["media"][1]["url"] == "https://help.aliyun.com/glass.pptx"

    asyncio.run(_run())


def test_enabled_list_gates_wan30(monkeypatch):
    import RH_ComfyUI.utils.backends.dashscope.config as dcfg

    monkeypatch.setattr(dcfg, "_cfg", lambda key: [] if key == "DashScope_Enabled_Models" else None)
    assert is_dashscope_model_enabled(DASHSCOPE_MODEL_WAN30) is False
    monkeypatch.setattr(
        dcfg,
        "_cfg",
        lambda key: ["wan3.0"] if key == "DashScope_Enabled_Models" else None,
    )
    assert is_dashscope_model_enabled("wan3.0") is True
    assert is_dashscope_model_enabled(DASHSCOPE_MODEL_HAPPYHORSE) is False


def test_model_check_available_respects_list(monkeypatch):
    import RH_ComfyUI.models.video.overrides as ov

    monkeypatch.setattr(ov, "is_dashscope_model_enabled", lambda name: False)
    assert asyncio.run(Wan30Def().check_available()) is False
    monkeypatch.setattr(ov, "is_dashscope_model_enabled", lambda name: name == "wan3.0")
    # 无 key 时通道仍不可用,但至少不再被列表提前挡住
    # 这里只断言列表开启后会继续问通道(返回 False 也行,只要不是列表短路的文案)
    reason = asyncio.run(Wan30Def().unavailable_reason())
    assert "启用的 DashScope 模型" not in reason or "wan3.0" in reason


def test_channel_translates_happyhorse_403_to_retryable_channel_error(monkeypatch):
    """HTTP 403 抛的是父类 HappyHorseProviderError,必须翻成可切通道的 ChannelError。"""
    import pytest

    from RH_ComfyUI.core.base.errors import ChannelError
    from RH_ComfyUI.utils.backends.wan30.channel import Wan30Channel
    from RH_ComfyUI.utils.backends.wan30.provider import Wan30Provider
    from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProviderError

    monkeypatch.setattr(
        "RH_ComfyUI.utils.backends.wan30.channel.dashscope_disabled_reason",
        lambda *_a, **_k: None,
    )

    class _P(Wan30Provider):
        name = "dashscope"

    ch = Wan30Channel(_P, dry_run_resolver=lambda: False)

    async def _boom(spec, *, model=None, on_progress=None):
        raise HappyHorseProviderError(
            "dashscope API 错误 403: AccessDenied",
            code="HTTP_ERROR",
            retryable=True,
            provider="dashscope",
            http_status=403,
            user_message="Access denied.",
        )

    ch._get_provider = lambda: type("Prov", (), {"api_key": "k", "run": staticmethod(_boom)})()

    with pytest.raises(ChannelError) as ei:
        asyncio.run(
            ch.invoke(
                request=GenerationRequest(task_type=TaskType.VIDEO, prompt="一只猫"),
                vendor_model="wan3.0-video",
            )
        )
    assert ei.value.retryable is True
    assert ei.value.channel == "dashscope"
    assert "Access denied" in (ei.value.user_message or "")


def test_run_failovers_to_next_channel_after_dashscope_403(monkeypatch):
    """官方 dashscope 403 后应切到下一绑定(网关),而不是整单失败。"""
    from RH_ComfyUI.core.base.errors import ChannelError
    from RH_ComfyUI.core.schema.types import NodeOutput
    from RH_ComfyUI.core.channels.channel import ChannelBinding, ProviderChannel
    from RH_ComfyUI.utils.backends.wan30.channel import Wan30Channel
    from RH_ComfyUI.utils.backends.wan30.provider import Wan30Provider
    from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProviderError

    monkeypatch.setattr(
        "RH_ComfyUI.utils.backends.wan30.channel.dashscope_disabled_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "RH_ComfyUI.rh_config.comfyui_config.plugin_dry_run",
        lambda: False,
    )

    class _P(Wan30Provider):
        name = "dashscope"

    dash = Wan30Channel(_P, dry_run_resolver=lambda: False)

    async def _boom(spec, *, model=None, on_progress=None):
        raise HappyHorseProviderError(
            "dashscope API 错误 403: AccessDenied",
            code="HTTP_ERROR",
            retryable=True,
            provider="dashscope",
            http_status=403,
            user_message="Access denied.",
        )

    dash._get_provider = lambda: type("Prov", (), {"api_key": "k", "run": staticmethod(_boom)})()

    async def _dash_ok() -> bool:
        return True

    dash.check_available = _dash_ok  # type: ignore[method-assign]

    class _Gateway(ProviderChannel):
        name = "gateway_slot1_wan30"

        async def check_available(self) -> bool:
            return True

        async def invoke(self, **kwargs):
            return NodeOutput(status="ok", output_type="video", data=b"from-gateway")

    class _Model(Wan30Def):
        def channel_bindings(self) -> list[ChannelBinding]:
            return [
                ChannelBinding(dash, vendor_model="wan3.0-video"),
                ChannelBinding(_Gateway(), vendor_model="wan3.0-video"),
            ]

        def validate(self, request: GenerationRequest) -> None:
            return None

        async def prepare_request(self, request: GenerationRequest) -> GenerationRequest:
            return request

    out = asyncio.run(
        _Model().run(GenerationRequest(task_type=TaskType.VIDEO, prompt="一只猫"))
    )
    assert out.data == b"from-gateway"
    # 确认不是 ChannelError 直接穿出
    assert not isinstance(out, ChannelError)
