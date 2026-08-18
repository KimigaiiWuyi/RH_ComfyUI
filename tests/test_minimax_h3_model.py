"""MiniMax H3 模型注册 / schema / 校验 / 分类 / 渲染 / 启用列表"""

import asyncio

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind, text_item, image_item
from RH_ComfyUI.models.video.defs import MiniMaxH3Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.spec import MediaRole, VideoTaskShape
from RH_ComfyUI.utils.backends.minimax.config import (
    MINIMAX_MODEL_H3,
    is_minimax_model_enabled,
)
from RH_ComfyUI.utils.mappers.minimax_h3_billing import estimate_minimax_h3_points
from RH_ComfyUI.utils.backends.minimax.h3_classify import (
    to_api_ratio,
    to_api_resolution,
    classify_minimax_h3,
)
from RH_ComfyUI.utils.backends.minimax.h3_provider import MiniMaxH3Provider


def test_node_name_and_schema():
    m = MiniMaxH3Def()
    assert m.name == "minimax_h3"
    schema = m.input_schema()
    assert schema["images"].max_items == 9
    assert schema["video_refs"].max_items == 3
    assert schema["audio_refs"].max_items == 3
    assert schema["task_mode"].values == ["auto", "t2v", "i2v", "first_last", "reference"]
    assert "first_frame" in (schema["frame_mode"].values or [])
    assert "last_frame" in (schema["frame_mode"].values or [])
    assert "768p" in (schema["resolution"].values or [])
    assert "2k" in (schema["resolution"].values or [])
    assert schema["duration"].minimum == 4
    assert schema["duration"].maximum == 15
    assert "generate_audio" not in schema
    assert m.supports_remote_cancel is True


def test_point_range_dynamic():
    m = MiniMaxH3Def()
    lo, hi = m.point_range()
    assert lo < hi
    assert lo == estimate_minimax_h3_points("768p", 4)
    assert hi == estimate_minimax_h3_points("2k", 15, input_video_duration=15.0)


def test_estimate_cost_reads_resolution_duration():
    m = MiniMaxH3Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="跑",
        resolution="768p",
        duration=8,
        params={"resolution": "768p", "duration": 8},
    )
    assert m.estimate_cost(req) == estimate_minimax_h3_points("768p", 8)


def test_validate_t2v_rejects_adaptive():
    m = MiniMaxH3Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="一只猫在草地上奔跑",
        ratio="adaptive",
        generate_audio=True,
    )
    try:
        m.validate(req)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "adaptive" in str(e)


def test_validate_accepts_t2v():
    m = MiniMaxH3Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="一只猫在草地上奔跑",
        ratio="16:9",
        resolution="2k",
        duration=5,
        generate_audio=True,
    )
    m.validate(req)


def test_validate_t2v_mode_rejects_images():
    m = MiniMaxH3Def()
    try:
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="x",
                ratio="16:9",
                images=[b"a"],
                params={"task_mode": "t2v"},
                generate_audio=True,
            )
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "文生" in str(e)


def test_validate_rejects_first_last_with_video():
    m = MiniMaxH3Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="x",
        images=[b"img1", b"img2"],
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/a.mp4")],
        params={"frame_mode": "first_last"},
        generate_audio=True,
    )
    try:
        m.validate(req)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "首尾帧" in str(e)


def test_channel_bindings_has_minimax_h3():
    m = MiniMaxH3Def()
    bindings = m.channel_bindings()
    assert bindings
    assert any(b.channel.name == "minimax-h3" for b in bindings)
    assert any(b.vendor_model == "MiniMax-H3" for b in bindings)


def test_classify_t2v_i2v_flf_r2v():
    t2v = classify_minimax_h3(
        GenerationRequest(task_type=TaskType.VIDEO, prompt="a", ratio="16:9")
    )
    assert t2v.shape == VideoTaskShape.TEXT2VIDEO

    i2v = classify_minimax_h3(
        GenerationRequest(task_type=TaskType.VIDEO, prompt="a", images=[b"x"])
    )
    assert i2v.shape == VideoTaskShape.IMAGE2VIDEO
    assert i2v.images()[0].role == MediaRole.FIRST_FRAME

    flf = classify_minimax_h3(
        GenerationRequest(task_type=TaskType.VIDEO, prompt="a", images=[b"a", b"b"])
    )
    assert flf.shape == VideoTaskShape.FIRST_LAST_FRAME
    assert flf.images()[0].role == MediaRole.FIRST_FRAME
    assert flf.images()[1].role == MediaRole.LAST_FRAME

    r2v = classify_minimax_h3(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="a",
            images=[b"a"],
            video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        )
    )
    assert r2v.shape == VideoTaskShape.MULTIMODAL
    assert r2v.images()[0].role == MediaRole.REFERENCE


def test_classify_explicit_four_modes():
    t2v = classify_minimax_h3(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="a",
            images=[b"x"],
            params={"task_mode": "t2v"},
        )
    )
    assert t2v.shape == VideoTaskShape.TEXT2VIDEO

    i2v = classify_minimax_h3(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="a",
            images=[b"x"],
            params={"task_mode": "i2v"},
        )
    )
    assert i2v.shape == VideoTaskShape.IMAGE2VIDEO
    assert i2v.images()[0].role == MediaRole.FIRST_FRAME

    last = classify_minimax_h3(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="a",
            images=[b"x"],
            params={"task_mode": "i2v", "frame_mode": "last_frame"},
        )
    )
    assert last.shape == VideoTaskShape.IMAGE2VIDEO
    assert last.images()[0].role == MediaRole.LAST_FRAME

    flf = classify_minimax_h3(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="a",
            images=[b"a", b"b"],
            params={"task_mode": "first_last"},
        )
    )
    assert flf.shape == VideoTaskShape.FIRST_LAST_FRAME

    ref = classify_minimax_h3(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="a",
            images=[b"a", b"b"],
            params={"task_mode": "reference"},
        )
    )
    assert ref.shape == VideoTaskShape.MULTIMODAL
    assert all(m.role == MediaRole.REFERENCE for m in ref.images())


def test_to_api_resolution_and_ratio():
    assert to_api_resolution("768p") == "768P"
    assert to_api_resolution("2k") == "2K"
    spec = classify_minimax_h3(
        GenerationRequest(task_type=TaskType.VIDEO, prompt="a", ratio="9:16")
    )
    assert to_api_ratio(spec) == "9:16"
    spec_i2v = classify_minimax_h3(
        GenerationRequest(task_type=TaskType.VIDEO, prompt="a", images=[b"x"], ratio="16:9")
    )
    assert to_api_ratio(spec_i2v) == "adaptive"


def test_render_create_t2v_body():
    async def _run():
        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="史诗级太空歌剧",
            resolution="2k",
            duration=5,
            ratio="16:9",
            watermark=False,
        )
        spec = classify_minimax_h3(req)
        p = MiniMaxH3Provider(api_key="test")
        method, url, headers, body = await p.render_create(spec, model="MiniMax-H3")
        assert method == "POST"
        assert url.endswith("/v2/video_generation")
        assert headers.get("Authorization") == "Bearer test"
        assert body["model"] == "MiniMax-H3"
        assert body["resolution"] == "2K"
        assert body["duration"] == 5
        assert body["ratio"] == "16:9"
        assert body["content"][0] == {"type": "text", "text": "史诗级太空歌剧"}
        assert "aigc_watermark" not in body

    asyncio.run(_run())


def test_render_create_i2v_first_frame():
    async def _run():
        from RH_ComfyUI.utils.backends.seedance.spec import SpecMedia

        spec = classify_minimax_h3(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="镜头推进",
                resolution="768p",
                duration=6,
            )
        )
        spec.media = [
            SpecMedia(
                kind=MediaKind.IMAGE,
                role=MediaRole.FIRST_FRAME,
                ref=MediaRef(kind=MediaKind.IMAGE, url="https://cdn.example.com/f.png"),
                index=1,
            )
        ]
        spec.shape = VideoTaskShape.IMAGE2VIDEO
        p = MiniMaxH3Provider(api_key="k")
        _method, _url, _headers, body = await p.render_create(spec, model="MiniMax-H3")
        assert body["resolution"] == "768P"
        assert body["ratio"] == "adaptive"
        assert body["content"][1]["type"] == "image_url"
        assert body["content"][1]["role"] == "first_frame"
        assert body["content"][1]["image_url"]["url"] == "https://cdn.example.com/f.png"

    asyncio.run(_run())


def test_render_create_last_frame_and_first_last():
    async def _run():
        last = classify_minimax_h3(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="镜头拉远",
                images=[b"x"],
                resolution="2k",
                duration=5,
                params={"task_mode": "i2v", "frame_mode": "last_frame"},
            )
        )
        last.images()[0].ref = MediaRef(kind=MediaKind.IMAGE, url="https://cdn.example.com/last.png")
        p = MiniMaxH3Provider(api_key="k")
        _m, _u, _h, body = await p.render_create(last, model="MiniMax-H3")
        assert body["content"][1]["role"] == "last_frame"
        assert body["ratio"] == "adaptive"

        flf = classify_minimax_h3(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="A little girl grows up.",
                images=[b"a", b"b"],
                resolution="2k",
                duration=5,
                params={"task_mode": "first_last"},
            )
        )
        flf.images()[0].ref = MediaRef(kind=MediaKind.IMAGE, url="https://cdn.example.com/f.png")
        flf.images()[1].ref = MediaRef(kind=MediaKind.IMAGE, url="https://cdn.example.com/l.png")
        _m, _u, _h, body2 = await p.render_create(flf, model="MiniMax-H3")
        roles = [c.get("role") for c in body2["content"] if c.get("type") == "image_url"]
        assert roles == ["first_frame", "last_frame"]

    asyncio.run(_run())


def test_parse_query_succeeded():
    p = MiniMaxH3Provider(api_key="k")
    task = p._parse_task(
        {
            "task": {
                "id": "424010985738629",
                "status": "succeeded",
                "content": {"url": "https://cdn.example.com/out.mp4"},
                "usage": {"total_seconds": 5, "output_seconds": 5},
            }
        }
    )
    assert task.id == "424010985738629"
    assert task.video_url == "https://cdn.example.com/out.mp4"
    assert task.status.value == "succeeded"


def test_enabled_list_default_empty(monkeypatch):
    class _Cfg:
        data: list[str] = []

    class _Service:
        def get_config(self, key: str):
            if key == "MiniMax_Enabled_Models":
                return _Cfg()
            raise KeyError(key)

    import RH_ComfyUI.utils.backends.minimax.config as cfg

    monkeypatch.setattr(
        "RH_ComfyUI.rh_config.comfyui_config.SERVICE_CONFIG",
        _Service(),
        raising=False,
    )
    # 直接 patch _cfg 更稳
    monkeypatch.setattr(cfg, "_cfg", lambda key: [] if key == "MiniMax_Enabled_Models" else None)
    assert is_minimax_model_enabled(MINIMAX_MODEL_H3) is False
    monkeypatch.setattr(cfg, "_cfg", lambda key: ["minimax_h3"] if key == "MiniMax_Enabled_Models" else None)
    assert is_minimax_model_enabled("minimax_h3") is True
    assert is_minimax_model_enabled("minimax_t2a_speech") is False


def test_validate_oc_only_i2v_and_rejects_extra_images():
    from RH_ComfyUI.core.schema.types import MediaRef as MR

    m = MiniMaxH3Def()
    oc_req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="镜头推进",
        params={"task_mode": "i2v"},
        ordered_content=[
            image_item(MR(kind=MediaKind.IMAGE, url="https://ex.com/f.png")),
        ],
        generate_audio=True,
    )
    m.validate(oc_req)

    try:
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="x",
                images=[b"a", b"b"],
                params={"task_mode": "i2v"},
                generate_audio=True,
            )
        )
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "1 张" in str(e)


def test_unavailable_reason_enabled_without_key(monkeypatch):
    from RH_ComfyUI.utils.backends.minimax import config as cfg, h3_channel as ch_mod

    monkeypatch.setattr(cfg, "_cfg", lambda key: ["minimax_h3"] if key == "MiniMax_Enabled_Models" else "")
    monkeypatch.setattr(ch_mod, "minimax_api_key", lambda: "")
    monkeypatch.setattr(ch_mod, "is_minimax_model_enabled", lambda name: name == "minimax_h3")
    reason = asyncio.run(MiniMaxH3Def().unavailable_reason())
    assert "未注册" not in reason
    assert "API Key" in reason or "apikey" in reason.lower()


def test_poll_returns_cancelled():
    from RH_ComfyUI.utils.backends.seedance.provider import NormalizedTask, NormalizedStatus

    p = MiniMaxH3Provider(api_key="k")

    async def _fake_get(task_id: str) -> NormalizedTask:
        return NormalizedTask(id=task_id, status=NormalizedStatus.CANCELLED)

    setattr(p, "get", _fake_get)

    async def _run() -> None:
        task = await p.poll_until_done("tid-1", interval=0.01)
        assert task.status == NormalizedStatus.CANCELLED

    asyncio.run(_run())


def test_render_merges_ordered_text():
    async def _run() -> None:
        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="主提示",
            resolution="2k",
            duration=5,
            ratio="16:9",
            ordered_content=[text_item("补充镜头说明")],
        )
        spec = classify_minimax_h3(req)
        p = MiniMaxH3Provider(api_key="k")
        _m, _u, _h, body = await p.render_create(spec, model="MiniMax-H3")
        text = body["content"][0]["text"]
        assert "主提示" in text
        assert "补充镜头说明" in text

    asyncio.run(_run())


def test_channel_unavailable_without_enable(monkeypatch):
    from RH_ComfyUI.utils.backends.minimax import h3_channel as ch_mod
    from RH_ComfyUI.utils.backends.minimax.h3_channel import MiniMaxH3Channel

    monkeypatch.setattr(ch_mod, "minimax_api_key", lambda: "sk-test")
    monkeypatch.setattr(ch_mod, "is_minimax_model_enabled", lambda name: False)
    ch = MiniMaxH3Channel()
    assert asyncio.run(ch.check_available()) is False
    reason = asyncio.run(ch.unavailable_reason())
    assert "minimax_h3" in reason


def test_catalog_available_flips_when_enable_list_changes(monkeypatch):
    """/models 每次 _build_entry → check_available,启用列表热改后即时刷新。"""
    from RH_ComfyUI.models.video import overrides as ov
    from RH_ComfyUI.rh_models.api import _build_entry
    from RH_ComfyUI.core.routing.registry import model_registry
    from RH_ComfyUI.utils.backends.minimax import h3_channel as ch_mod

    enabled: list[str] = []
    monkeypatch.setattr(ov, "is_minimax_model_enabled", lambda name: name in enabled)
    monkeypatch.setattr(ch_mod, "is_minimax_model_enabled", lambda name: name in enabled)
    monkeypatch.setattr(ch_mod, "minimax_api_key", lambda: "sk-test")

    model = MiniMaxH3Def()
    prev = model_registry.get(model.name)
    model_registry.register(model)
    try:
        node = model.node
        entry_off = asyncio.run(_build_entry(node))
        assert entry_off.available is False
        enabled.append("minimax_h3")
        entry_on = asyncio.run(_build_entry(node))
        assert entry_on.available is True
    finally:
        if prev is not None:
            model_registry.register(prev)
        else:
            model_registry.unregister(model.name)


def test_invoke_refuses_when_disabled(monkeypatch):
    from RH_ComfyUI.core.base.errors import ChannelError
    from RH_ComfyUI.utils.backends.minimax import h3_channel as ch_mod
    from RH_ComfyUI.utils.backends.minimax.h3_channel import MiniMaxH3Channel

    monkeypatch.setattr(ch_mod, "minimax_api_key", lambda: "sk-test")
    monkeypatch.setattr(ch_mod, "is_minimax_model_enabled", lambda name: False)
    monkeypatch.setattr(
        ch_mod,
        "minimax_disabled_reason",
        lambda name, display: f"{display} 未启用 {name}",
    )
    ch = MiniMaxH3Channel()
    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="猫", ratio="16:9")
    try:
        asyncio.run(ch.invoke(request=req))
    except ChannelError as exc:
        assert exc.retryable is True
        assert "minimax_h3" in str(exc)
    else:
        raise AssertionError("disabled MiniMax H3 channel must refuse invoke")


def test_supports_request_fail_closed_on_classify_error(monkeypatch):
    from RH_ComfyUI.utils.backends.minimax.h3_channel import MiniMaxH3Channel

    ch = MiniMaxH3Channel()

    def _boom(_req: GenerationRequest) -> None:
        raise ValueError("bad spec")

    monkeypatch.setattr(
        "RH_ComfyUI.utils.backends.minimax.h3_channel.classify_minimax_h3",
        _boom,
    )
    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="猫", ratio="16:9")
    assert ch.supports_request(req) is False


def test_prepare_request_rejects_unprobeable_and_short_audio(monkeypatch):
    async def _unprobeable(data: bytes, **_kwargs):
        return data, 0.0, None, "audio/mpeg"

    monkeypatch.setattr(
        "RH_ComfyUI.utils.audio_process.clamp_minimax_h3_ref_audio",
        _unprobeable,
    )
    m = MiniMaxH3Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="配乐",
        audio_refs=[MediaRef(kind=MediaKind.AUDIO, data=b"ID3xxxx")],
        params={"task_mode": "reference"},
        generate_audio=True,
    )
    try:
        asyncio.run(m.prepare_request(req))
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "无法探测" in str(e)

    async def _too_short(data: bytes, **_kwargs):
        return data, 1.5, None, "audio/mpeg"

    monkeypatch.setattr(
        "RH_ComfyUI.utils.audio_process.clamp_minimax_h3_ref_audio",
        _too_short,
    )
    try:
        asyncio.run(m.prepare_request(req))
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "≥ 2" in str(e) or "2 秒" in str(e)


def test_run_sets_wire_then_binds_vendor_cancel():
    from RH_ComfyUI.core.dispatch.active_tasks import get_active_task_registry
    from RH_ComfyUI.core.telemetry.wire_capture import get_wire_audit, clear_wire_audit
    from RH_ComfyUI.utils.backends.seedance.provider import NormalizedTask, NormalizedStatus

    p = MiniMaxH3Provider(api_key="k")

    async def _fake_request(method: str, url: str, **_kwargs):
        assert method == "POST"
        assert url.endswith("/v2/video_generation")
        return {"task_id": "tid-h3-wire"}

    async def _fake_poll(task_id: str, **_kwargs):
        return NormalizedTask(
            id=task_id,
            status=NormalizedStatus.SUCCEEDED,
            video_url="https://cdn.example.com/out.mp4",
        )

    setattr(p, "_request", _fake_request)
    setattr(p, "poll_until_done", _fake_poll)

    async def _run() -> None:
        clear_wire_audit()
        reg = get_active_task_registry()
        ag = await reg.register(model_name="minimax_h3", trace_id="h3-wire-1")
        try:
            spec = classify_minimax_h3(
                GenerationRequest(
                    task_type=TaskType.VIDEO,
                    prompt="史诗级太空歌剧",
                    resolution="2k",
                    duration=5,
                    ratio="16:9",
                )
            )
            task = await p.run(spec, model="MiniMax-H3")
            assert task.id == "tid-h3-wire"
            snap = get_wire_audit()
            assert snap["prompt"] == "史诗级太空歌剧"
            body = snap["request"]
            assert isinstance(body, dict)
            assert body["model"] == "MiniMax-H3"
            assert body["resolution"] == "2K"
            assert ag.vendor_task_id == "tid-h3-wire"
            assert ag.channel_name == "minimax-h3"
            assert ag.cancel_remote is not None
        finally:
            await reg.unregister(ag)
            clear_wire_audit()

    asyncio.run(_run())
