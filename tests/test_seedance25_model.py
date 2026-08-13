"""Seedance 2.5 模型契约:schema / 校验 / 分类 / ARK 请求体"""

from __future__ import annotations

import asyncio

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.models.video.defs import Seedance25Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.spec import VideoTaskShape
from RH_ComfyUI.utils.backends.seedance.classify import classify_video_spec
from RH_ComfyUI.utils.backends.seedance.providers.ark import ArkSeedanceProvider


def test_node_def_identity_and_backend_model():
    node = Seedance25Def.node_def()
    assert node.name == "seedance2.5"
    assert node.backend == "seedance"
    assert node.backend_model == "doubao-seedance-2-5-260628"
    assert node.backend_models == {"ark": "doubao-seedance-2-5-260628"}
    # 类型与 2.0 分开:不挂 runninghub
    assert "runninghub" not in (node.backend_models or {})


def test_schema_ports_and_limits():
    node = Seedance25Def.node_def()
    for port in (
        "prompt",
        "images",
        "video_refs",
        "audio_refs",
        "task_mode",
        "frame_mode",
        "ratio",
        "resolution",
        "duration",
        "output_format",
        "omni_reference_task_type",
        "generate_audio",
    ):
        assert port in node.inputs, f"缺少端口 {port}"

    # 官方 camera_fixed 仅 1.x;2.5 暴露会让前端展示「固定镜头」并触发 400
    assert "camera_fixed" not in node.inputs

    assert node.inputs["images"].max_items == 30
    assert node.inputs["video_refs"].max_items == 10
    assert node.inputs["audio_refs"].max_items == 10
    assert node.inputs["resolution"].values == ["480p", "720p"]
    assert node.inputs["duration"].minimum == -1
    assert node.inputs["duration"].maximum == 30
    assert node.inputs["output_format"].values == ["mp4", "mov"]
    assert node.inputs["task_mode"].values == ["auto", "edit", "extend"]
    assert node.inputs["omni_reference_task_type"].values == ["auto", "edit", "extend"]
    assert node.inputs["omni_reference_task_type"].default == "auto"
    assert node.inputs["ratio"].default == "adaptive"


def test_channel_bindings_only_ark():
    m = Seedance25Def()
    bindings = m.channel_bindings()
    names = {b.channel.name for b in bindings}
    assert "ark" in names
    assert "runninghub" not in names
    ark_b = next(b for b in bindings if b.channel.name == "ark")
    assert ark_b.vendor_model == "doubao-seedance-2-5-260628"


def test_reject_1080p():
    m = Seedance25Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="一只猫",
        resolution="1080p",
        duration=5,
        ratio="adaptive",
    )
    with pytest.raises(ValidationError, match="1080p|分辨率"):
        m.validate(req)


def test_reject_too_many_videos():
    m = Seedance25Def()
    refs = [MediaRef(kind=MediaKind.VIDEO, url=f"https://ex.com/{i}.mp4") for i in range(11)]
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="多视频",
        video_refs=refs,
        duration=5,
        ratio="adaptive",
        params={"frame_mode": "reference"},
    )
    with pytest.raises(ValidationError, match="最多 10"):
        m.validate(req)


def test_edit_requires_video_and_adaptive_ratio():
    m = Seedance25Def()
    # 无视频
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="编辑",
        duration=-1,
        ratio="adaptive",
        params={"task_mode": "edit"},
    )
    with pytest.raises(ValidationError, match="参考视频"):
        m.validate(req)

    # 有视频但固定比例
    req2 = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="编辑",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=-1,
        ratio="16:9",
        params={"task_mode": "edit"},
    )
    with pytest.raises(ValidationError, match="adaptive"):
        m.validate(req2)


def test_first_frame_requires_adaptive_ratio():
    m = Seedance25Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="图生",
        images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 16],
        duration=5,
        ratio="9:16",
    )
    with pytest.raises(ValidationError, match="adaptive"):
        m.validate(req)


def test_text2video_allows_custom_ratio():
    m = Seedance25Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生视频",
        duration=10,
        resolution="720p",
        ratio="16:9",
    )
    m.validate(req)  # 不抛


def test_reject_camera_fixed():
    m = Seedance25Def()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="固定镜头",
        duration=5,
        ratio="adaptive",
        camera_fixed=True,
    )
    with pytest.raises(ValidationError, match="camera_fixed|固定镜头"):
        m.validate(req)


def test_classify_task_mode_edit():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="把背景换成海边",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=-1,
        ratio="adaptive",
        params={"task_mode": "edit", "output_format": "mov"},
    )
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.VIDEO_EDIT
    assert spec.duration == -1
    assert spec.output_format == "mov"
    assert spec.omni_reference_task_type == "edit"


def test_classify_task_mode_extend():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="延长视频1",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=11,
        ratio="adaptive",
        params={"task_mode": "extend"},
    )
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.VIDEO_EXTEND
    assert spec.duration == 11
    assert spec.omni_reference_task_type == "extend"


def test_ark_render_includes_duration_minus_one_and_output_format():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="编辑",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=-1,
        ratio="adaptive",
        generate_audio=True,
        params={"task_mode": "edit", "output_format": "mov"},
    )
    spec = classify_video_spec(req)
    p = ArkSeedanceProvider(api_key="test-key")
    method, url, headers, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2-5-260628"))
    assert method == "POST"
    assert body["model"] == "doubao-seedance-2-5-260628"
    assert body["duration"] == -1
    assert body["ratio"] == "adaptive"
    assert body["output_format"] == "mov"
    assert body.get("generate_audio") is True
    assert body["omni_reference_task_type"] == "edit"
    # content 含 reference_video
    roles = [c.get("role") for c in body["content"] if c.get("type") != "text"]
    assert "reference_video" in roles


def test_ark_render_omits_default_mp4_output_format():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生",
        duration=5,
        ratio="16:9",
        params={"output_format": "mp4"},
    )
    spec = classify_video_spec(req)
    p = ArkSeedanceProvider(api_key="test-key")
    _m, _u, _h, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2-5-260628"))
    assert "output_format" not in body


def test_ark_render_omits_camera_fixed_for_seedance25():
    """存量请求即使带 camera_fixed=true,2.5 请求体也不得写入(上游 400)。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生",
        duration=5,
        ratio="16:9",
        camera_fixed=True,
    )
    spec = classify_video_spec(req)
    assert spec.camera_fixed is True
    p = ArkSeedanceProvider(api_key="test-key")
    for model in ("doubao-seedance-2-5-260628", "doubao-seedance-2.5"):
        _m, _u, _h, body = asyncio.run(p.render_create(spec, model=model))
        assert "camera_fixed" not in body, model


def test_ark_render_keeps_camera_fixed_for_seedance2():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生",
        duration=5,
        ratio="16:9",
        camera_fixed=True,
    )
    spec = classify_video_spec(req)
    p = ArkSeedanceProvider(api_key="test-key")
    _m, _u, _h, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2-0-260128"))
    assert body.get("camera_fixed") is True


def test_classify_explicit_omni_reference_task_type_wins():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生",
        duration=5,
        ratio="16:9",
        params={"omni_reference_task_type": "extend"},
    )
    spec = classify_video_spec(req)
    assert spec.omni_reference_task_type == "extend"


def test_classify_top_level_omni_reference_task_type():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="编辑",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=-1,
        ratio="adaptive",
        omni_reference_task_type="edit",
    )
    spec = classify_video_spec(req)
    assert spec.omni_reference_task_type == "edit"


def test_ark_render_omni_reference_task_type_only_for_seedance25():
    spec = classify_video_spec(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="延长视频1",
            video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
            duration=8,
            ratio="adaptive",
            params={"task_mode": "extend"},
        )
    )
    p = ArkSeedanceProvider(api_key="test-key")
    _m, _u, _h, body25 = asyncio.run(p.render_create(spec, model="doubao-seedance-2-5-260628"))
    assert body25["omni_reference_task_type"] == "extend"
    _m, _u, _h, body20 = asyncio.run(p.render_create(spec, model="doubao-seedance-2-0-260128"))
    assert "omni_reference_task_type" not in body20


def test_ark_render_omits_omni_for_text_and_frame_shapes():
    """文生 / 首帧 / 首尾帧不得写 omni_reference_task_type(上游 TaskTypeConstraint)。"""
    p = ArkSeedanceProvider(api_key="test-key")
    cases = [
        GenerationRequest(task_type=TaskType.VIDEO, prompt="文生", duration=5, ratio="16:9"),
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="首帧",
            images=[b"IMG"],
            duration=5,
        ),
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="首尾帧",
            images=[b"A", b"B"],
            duration=5,
            params={"frame_mode": "first_last"},
        ),
    ]
    for req in cases:
        spec = classify_video_spec(req)
        assert spec.shape in (
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.FIRST_LAST_FRAME,
        )
        _m, _u, _h, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2.5"))
        assert "omni_reference_task_type" not in body, spec.shape


def test_ark_render_omni_auto_for_seedance25_multimodal():
    spec = classify_video_spec(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="多参考",
            images=[b"IMG"],
            duration=5,
            ratio="16:9",
            params={"frame_mode": "reference"},
        )
    )
    assert spec.shape == VideoTaskShape.MULTIMODAL
    p = ArkSeedanceProvider(api_key="test-key")
    _m, _u, _h, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2.5"))
    assert body["omni_reference_task_type"] == "auto"


def test_ark_media_limits_allow_seedance25():
    p = ArkSeedanceProvider(api_key="x")
    assert p.max_images >= 30
    assert p.max_videos >= 10
    assert p.max_audios >= 10
    assert p.max_duration >= 30
