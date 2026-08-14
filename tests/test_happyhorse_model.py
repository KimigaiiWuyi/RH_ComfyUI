"""HappyHorse 1.1 模型注册 / schema / estimate"""

import asyncio

from RH_ComfyUI.models.video.defs import HappyHorse11Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.happyhorse_billing import estimate_happyhorse_points


def test_node_name_and_schema():
    m = HappyHorse11Def()
    assert m.name == "happyhorse1.1"
    schema = m.input_schema()
    assert "prompt" in schema
    assert "images" in schema
    assert schema["images"].max_items == 9
    assert "video_refs" in schema
    assert schema["video_refs"].max_items == 1
    assert "frame_mode" in schema
    assert "auto" in (schema["frame_mode"].values or [])
    assert "reference" in (schema["frame_mode"].values or [])
    assert "task_mode" in schema
    assert "edit" in (schema["task_mode"].values or [])
    assert "extend" not in (schema["task_mode"].values or [])
    assert "480p" in (schema["resolution"].values or [])
    # 不暴露 generate_audio / audio_refs
    assert "generate_audio" not in schema
    assert "audio_refs" not in schema


def test_point_range_dynamic():
    m = HappyHorse11Def()
    lo, hi = m.point_range()
    assert lo < hi
    assert lo == estimate_happyhorse_points("480p", 3)
    assert hi == estimate_happyhorse_points("1080p", 15)


def test_estimate_cost_reads_resolution_duration():
    m = HappyHorse11Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="跑",
        resolution="1080p",
        duration=10,
        params={"resolution": "1080p", "duration": 10},
    )
    assert m.estimate_cost(req) == estimate_happyhorse_points("1080p", 10)


def test_validate_rejects_audio_refs():
    from RH_ComfyUI.core.base.errors import ValidationError
    from RH_ComfyUI.core.schema.types import MediaRef, MediaKind

    m = HappyHorse11Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="x",
        audio_refs=[MediaRef(kind=MediaKind.AUDIO, url="https://ex.com/a.mp3")],
        generate_audio=False,
    )
    try:
        m.validate(req)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "音频" in str(e)


def test_validate_accepts_t2v():
    m = HappyHorse11Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="一只猫在草地上奔跑",
        generate_audio=True,  # 默认 True,模型应放行
    )
    m.validate(req)  # 不抛


def test_channel_bindings_has_dashscope():
    m = HappyHorse11Def()
    bindings = m.channel_bindings()
    assert bindings
    assert any(b.channel.name == "dashscope" for b in bindings)


def test_pick_vendor_message_priority_message_msg_code_status():
    from RH_ComfyUI.utils.backends.happyhorse.provider import _pick_vendor_message

    enabled = "当前key未启用该模型:happyhorse-1.1-r2v"
    # message 优先于 msg / code
    assert (
        _pick_vendor_message(
            {"code": 500, "message": enabled, "msg": "ignored"},
            http_status=502,
        )
        == enabled
    )
    # 无 message → msg
    assert (
        _pick_vendor_message(
            {"code": 500, "msg": enabled},
            http_status=502,
        )
        == enabled
    )
    # 无 message / msg → code
    assert _pick_vendor_message({"code": 500}, http_status=502) == "500"
    # 再没有 → HTTP status
    assert _pick_vendor_message({}, http_status=502) == "502"


def test_render_create_t2v_body():
    """Provider 文生请求体:无 media, resolution 大写 P。"""

    async def _run():
        from RH_ComfyUI.utils.backends.happyhorse.classify import classify_happyhorse
        from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProvider

        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="sunset over the sea",
            resolution="720p",
            duration=5,
            ratio="16:9",
            watermark=False,
        )
        spec = classify_happyhorse(req)
        p = HappyHorseProvider(api_key="test")
        method, url, headers, body = await p.render_create(spec, model="happyhorse-1.1-t2v")
        assert method == "POST"
        assert url.endswith("/services/aigc/video-generation/video-synthesis")
        assert headers.get("X-DashScope-Async") == "enable"
        assert body["model"] == "happyhorse-1.1-t2v"
        assert body["input"]["prompt"] == "sunset over the sea"
        assert "media" not in body["input"]
        assert body["parameters"]["resolution"] == "720P"
        assert body["parameters"]["duration"] == 5
        assert body["parameters"]["ratio"] == "16:9"
        assert body["parameters"]["watermark"] is False

    asyncio.run(_run())


def test_render_create_i2v_media_type():
    async def _run():
        from RH_ComfyUI.utils.backends.happyhorse.classify import classify_happyhorse
        from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProvider

        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="猫奔跑",
            images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 32],
            resolution="1080p",
            duration=5,
        )
        spec = classify_happyhorse(req)
        p = HappyHorseProvider(api_key="test")
        _m, _u, _h, body = await p.render_create(spec, model="happyhorse-1.1-i2v")
        media = body["input"]["media"]
        assert len(media) == 1
        assert media[0]["type"] == "first_frame"
        assert media[0]["url"].startswith("data:")
        assert "ratio" not in body["parameters"]  # i2v 跟随首帧
        assert body["parameters"]["resolution"] == "1080P"

    asyncio.run(_run())


def test_render_create_r2v_reference_images():
    async def _run():
        from RH_ComfyUI.utils.backends.happyhorse.classify import classify_happyhorse
        from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProvider

        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="图片1中的角色看向图片2",
            images=[b"A", b"B"],
            resolution="720p",
            duration=5,
            ratio="9:16",
        )
        spec = classify_happyhorse(req)
        p = HappyHorseProvider(api_key="test")
        _m, _u, _h, body = await p.render_create(spec, model="happyhorse-1.1-r2v")
        assert body["model"] == "happyhorse-1.1-r2v"
        assert body["input"]["prompt"] == "[Image 1]中的角色看向[Image 2]"
        assert len(body["input"]["media"]) == 2
        assert all(x["type"] == "reference_image" for x in body["input"]["media"])
        assert body["parameters"]["ratio"] == "9:16"

    asyncio.run(_run())


def test_validate_rejects_video_outside_edit():
    from RH_ComfyUI.core.base.errors import ValidationError
    from RH_ComfyUI.core.schema.types import MediaRef, MediaKind

    m = HappyHorse11Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="参考这段视频",
        images=[b"A"],
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/ref.mp4")],
        generate_audio=True,
        params={"frame_mode": "reference"},
    )
    try:
        m.validate(req)
        raise AssertionError("expected ValidationError")
    except ValidationError as e:
        assert "视频编辑" in str(e)


def test_render_create_r2v_never_emits_video_type():
    """多参考即使 request 里挂了视频,出站 media 也只能是 reference_image。"""

    async def _run():
        from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
        from RH_ComfyUI.utils.backends.happyhorse.classify import classify_happyhorse
        from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProvider

        req = GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="图片1中的角色奔跑",
            images=[b"A"],
            video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/ref.mp4")],
            resolution="720p",
            duration=5,
            params={"frame_mode": "reference"},
        )
        spec = classify_happyhorse(req)
        p = HappyHorseProvider(api_key="test")
        _m, _u, _h, body = await p.render_create(spec, model="happyhorse-1.1-r2v")
        types = [x["type"] for x in body["input"]["media"]]
        assert types == ["reference_image"]

    asyncio.run(_run())
