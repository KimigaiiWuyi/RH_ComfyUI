"""Seedance 2.5 Flash 模型契约:身份 / schema / 校验 / ARK·fal 请求体 / Flash 费率"""

from __future__ import annotations

import asyncio

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.models.video.defs import Seedance25Def, Seedance25FlashDef
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.spec import VideoTaskShape
from RH_ComfyUI.utils.mappers.seedance_billing import (
    settle_seedance25_flash_points,
    estimate_seedance25_flash_points,
)
from RH_ComfyUI.utils.backends.seedance.classify import classify_video_spec
from RH_ComfyUI.utils.backends.seedance.providers.ark import ArkSeedanceProvider


@pytest.fixture(autouse=True)
def _seedance_vendors_all_enabled(monkeypatch: pytest.MonkeyPatch):
    """启用列表热读宿主配置;绑定组装与本机勾选无关,一律视为全启用。"""
    monkeypatch.setattr(
        "RH_ComfyUI.models.video.overrides.is_seedance_model_enabled_on",
        lambda model, vendor: True,
    )


def _png(tag: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + tag


def test_node_def_identity_and_backend_model():
    node = Seedance25FlashDef.node_def()
    assert node.name == "seedance2.5_flash"
    assert node.backend == "seedance"
    assert node.backend_model == "doubao-seedance-2-5-flash-261115"
    assert node.backend_models == {"ark": "doubao-seedance-2-5-flash-261115"}
    # 与正常 2.5 是完全不同的上游模型
    assert Seedance25Def.node_def().backend_model == "doubao-seedance-2-5-260628"


def test_schema_ports_and_limits():
    node = Seedance25FlashDef.node_def()
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

    # Flash 不支持样片/成片:draft 端口不存在
    assert "draft" not in node.inputs
    assert "draft_task_id" not in node.inputs
    assert "camera_fixed" not in node.inputs

    # 官方上限:图 10 / 视频 5 / 音频 5(合计 20)
    assert node.inputs["images"].max_items == 10
    assert node.inputs["video_refs"].max_items == 5
    assert node.inputs["audio_refs"].max_items == 5
    # 仅 480p / 720p(无 1080p)
    assert node.inputs["resolution"].values == ["480p", "720p"]
    assert node.inputs["duration"].minimum == -1
    assert node.inputs["duration"].maximum == 30


def test_model_caps_and_resolutions():
    m = Seedance25FlashDef()
    assert m.MAX_IMAGES == 10
    assert m.MAX_VIDEOS == 5
    assert m.MAX_AUDIOS == 5
    assert m.max_reference_total == 20
    assert m.supported_resolutions == ["480p", "720p"]


def test_channel_bindings_ark_and_fal():
    m = Seedance25FlashDef()
    bindings = m.channel_bindings()
    names = {b.channel.name for b in bindings}
    assert "ark" in names
    assert "fal" in names
    assert "runninghub" not in names
    ark_b = next(b for b in bindings if b.channel.name == "ark")
    assert ark_b.vendor_model == "doubao-seedance-2-5-flash-261115"
    fal_b = next(b for b in bindings if b.channel.name == "fal")
    assert fal_b.vendor_model == "bytedance/seedance-2.5/flash"


def test_accept_720p_and_reject_1080p():
    m = Seedance25FlashDef()
    m.validate(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="一只猫",
            resolution="720p",
            duration=5,
            ratio="adaptive",
        )
    )
    with pytest.raises(ValidationError, match="480p|720p|分辨率"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="一只猫",
                resolution="1080p",
                duration=5,
                ratio="adaptive",
            )
        )


def test_reject_draft_and_final():
    """Flash 没有样片/成片:draft 与 draft_task_id 都在模型层拦下。"""
    m = Seedance25FlashDef()
    with pytest.raises(ValidationError, match="样片|成片"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="样片",
                resolution="480p",
                duration=5,
                params={"draft": True},
            )
        )
    with pytest.raises(ValidationError, match="样片|成片"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="",
                model="seedance2.5_flash",
                resolution="720p",
                duration=5,
                channel="ark",
                params={"draft_task_id": "cgt-2026-draft"},
            )
        )


def test_reject_over_cap_references():
    m = Seedance25FlashDef()
    # 11 张图 > 10
    with pytest.raises(ValidationError, match="最多 10"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="多图",
                images=[_png(bytes([i])) for i in range(11)],
                duration=5,
                ratio="16:9",
                params={"frame_mode": "reference"},
            )
        )
    # 6 段视频 > 5
    with pytest.raises(ValidationError, match="最多 5"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="多视频",
                video_refs=[MediaRef(kind=MediaKind.VIDEO, url=f"https://ex.com/{i}.mp4") for i in range(6)],
                duration=5,
                ratio="adaptive",
                params={"frame_mode": "reference"},
            )
        )
    # 6 段音频 > 5
    with pytest.raises(ValidationError, match="最多 5"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="多音频",
                images=[_png()],
                audio_refs=[MediaRef(kind=MediaKind.AUDIO, url=f"https://ex.com/{i}.mp3") for i in range(6)],
                duration=5,
                ratio="16:9",
                params={"frame_mode": "reference"},
            )
        )
    # 合计 > 20(10 图 + 5 视频 + 5 音频 + 1 图 = 21)
    with pytest.raises(ValidationError, match="超过.*上限|最多 10"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="爆总量",
                images=[_png(bytes([i])) for i in range(11)],
                video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
                audio_refs=[MediaRef(kind=MediaKind.AUDIO, url="https://ex.com/a.mp3")],
                duration=5,
                ratio="16:9",
                params={"frame_mode": "reference"},
            )
        )


def test_duration_bounds():
    m = Seedance25FlashDef()
    m.validate(GenerationRequest(task_type=TaskType.VIDEO, prompt="文生", duration=-1, ratio="16:9"))
    m.validate(GenerationRequest(task_type=TaskType.VIDEO, prompt="文生", duration=30, ratio="16:9"))
    with pytest.raises(ValidationError, match="duration|时长|30"):
        m.validate(GenerationRequest(task_type=TaskType.VIDEO, prompt="文生", duration=31, ratio="16:9"))
    with pytest.raises(ValidationError, match="duration|时长|4"):
        m.validate(GenerationRequest(task_type=TaskType.VIDEO, prompt="文生", duration=3, ratio="16:9"))


def test_edit_requires_video_and_adaptive_ratio():
    m = Seedance25FlashDef()
    with pytest.raises(ValidationError, match="参考视频"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="编辑",
                duration=-1,
                ratio="adaptive",
                params={"task_mode": "edit"},
            )
        )
    with pytest.raises(ValidationError, match="adaptive"):
        m.validate(
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="编辑",
                video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
                duration=-1,
                ratio="16:9",
                params={"task_mode": "edit"},
            )
        )
    # adaptive + 有参考视频 → 合法
    m.validate(
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="编辑",
            video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
            duration=-1,
            ratio="adaptive",
            params={"task_mode": "edit"},
        )
    )


def test_ark_render_uses_flash_model_id():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生视频",
        duration=5,
        ratio="16:9",
        generate_audio=True,
    )
    spec = classify_video_spec(req)
    p = ArkSeedanceProvider(api_key="test-key")
    method, url, headers, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2-5-flash-261115"))
    assert method == "POST"
    assert body["model"] == "doubao-seedance-2-5-flash-261115"
    assert body["duration"] == 5
    assert "camera_fixed" not in body
    assert "draft" not in body


def test_ark_render_flash_edit_and_auto_duration():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="编辑视频1",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=-1,
        ratio="adaptive",
        params={"task_mode": "edit"},
    )
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.VIDEO_EDIT
    p = ArkSeedanceProvider(api_key="test-key")
    _m, _u, _h, body = asyncio.run(p.render_create(spec, model="doubao-seedance-2-5-flash-261115"))
    assert body["model"] == "doubao-seedance-2-5-flash-261115"
    assert body["duration"] == -1
    assert body["omni_reference_task_type"] == "edit"


def test_flash_estimate_points():
    """Flash 费率:无输入 46 元/M、有输入 28 元/M;token 公式同 2.5。"""
    # 720p 5s 无输入:108000 tokens × 46 元/M = 4.97 元 = 497
    assert estimate_seedance25_flash_points("720p", 5) == 497
    # 480p 4s 无输入:38430 tokens × 46 = 1.77 元 = 177
    assert estimate_seedance25_flash_points("480p", 4) == 177
    # 720p 5s + 输入 15s:(15+5)×21600=432000 × 28 = 12.10 元 = 1210
    assert estimate_seedance25_flash_points("720p", 5, input_video_duration=15.0) == 1210
    # 输入视频低于 4s 按 4s 计:(4+5)×21600=194400 × 28 = 5.44 元 = 544
    assert estimate_seedance25_flash_points("720p", 5, input_video_duration=2.0) == 544


def test_flash_settle_points():
    # 无输入:432000 相对 720p 5s(216000)超阈值 → 有输入 → ×28
    assert settle_seedance25_flash_points({"total_tokens": 432000}, "720p", output_duration=5.0) == 1210
    # 纯输出 token → 无输入 → ×46
    assert settle_seedance25_flash_points({"total_tokens": 108000}, "720p", output_duration=5.0) == 497
    # 无 usage → None(交给 point_cost 兜底)
    assert settle_seedance25_flash_points(None, "720p") is None


def test_model_estimate_and_settle_cost():
    m = Seedance25FlashDef()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生",
        resolution="720p",
        duration=5,
        ratio="16:9",
    )
    assert m.estimate_cost(req) == 497
    req_input = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="延长",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        duration=5,
        ratio="adaptive",
        params={"task_mode": "extend", "input_video_duration": 15.0},
    )
    assert m.estimate_cost(req_input) == 1210
    lo, hi = m.point_range()
    assert lo == estimate_seedance25_flash_points("480p", 4, video_refs=None)
    assert hi > lo
    # 后结算用 Flash 费率
    settled = m.settle_cost(req, {"total_tokens": 108000, "duration": 5})
    assert settled == 497


def test_fal_payload_flash_endpoints_and_caps():
    from RH_ComfyUI.utils.backends.fal.payload import prepare

    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="文生视频",
        duration=-1,
        resolution="720p",
        ratio="16:9",
        generate_audio=True,
    )
    sub = prepare("seedance2.5_flash", req)
    assert sub.path == "/bytedance/seedance-2.5/flash/text-to-video"
    assert sub.app == "bytedance/seedance-2.5/flash"
    assert sub.body["duration"] == "auto"
    assert sub.body["resolution"] == "720p"
    assert "draft" not in sub.body
    assert "task" not in sub.body

    # 参考端点:上限 10/5/5
    req_ref = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="多参考",
        images=[_png()],
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        audio_refs=[MediaRef(kind=MediaKind.AUDIO, url="https://ex.com/a.mp3")],
        duration=5,
        ratio="16:9",
        params={"frame_mode": "reference"},
    )
    sub_ref = prepare("seedance2.5_flash", req_ref)
    assert sub_ref.path == "/bytedance/seedance-2.5/flash/reference-to-video"
    assert sub_ref.body["task"] == "auto"

    # 6 段视频 > 5
    with pytest.raises(ValidationError, match="最多 5"):
        prepare(
            "seedance2.5_flash",
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="多视频",
                video_refs=[MediaRef(kind=MediaKind.VIDEO, url=f"https://ex.com/{i}.mp4") for i in range(6)],
                duration=5,
                ratio="adaptive",
                params={"frame_mode": "reference"},
            ),
        )
    # 1080p 拒绝
    with pytest.raises(ValidationError, match="480p|720p"):
        prepare(
            "seedance2.5_flash",
            GenerationRequest(
                task_type=TaskType.VIDEO,
                prompt="文生",
                duration=5,
                resolution="1080p",
            ),
        )
    # draft 不写进 body(模型层已拦,通道层也不再透传)
    sub_draft = prepare(
        "seedance2.5_flash",
        GenerationRequest(
            task_type=TaskType.VIDEO,
            prompt="样片",
            duration=5,
            params={"draft": True},
        ),
        for_estimate=True,
    )
    assert "draft" not in sub_draft.body


def test_estimate_api_accepts_flash_model():
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.rh_models.api import estimate_model_points
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.core.routing.registry import model_registry

    init_backends()
    discover_builtin_models()
    try:
        r = asyncio.run(estimate_model_points("seedance2.5_flash", resolution="720p", duration=5))
        assert r["point_cost"] == 497
        r_in = asyncio.run(
            estimate_model_points(
                "seedance2.5_flash",
                resolution="720p",
                duration=5,
                num_video_refs=1,
                input_video_duration=15.0,
            )
        )
        assert r_in["point_cost"] == 1210
    finally:
        model_registry.clear()


def test_reconcile_dispatch_for_flash():
    from RH_ComfyUI.core.billing.reconcile import actual_points_for_seedance_record

    raw = {
        "id": "cgt-flash",
        "model": "doubao-seedance-2-5-flash-261115",
        "status": "succeeded",
        "usage": {"completion_tokens": 108000, "total_tokens": 108000},
        "resolution": "720p",
        "duration": 5,
    }
    pts = actual_points_for_seedance_record(
        task_name="seedance2.5_flash",
        raw_response=raw,
        resolution="720p",
        duration_seconds=5,
        request_body={"duration": 5, "resolution": "720p", "content": [{"type": "text", "text": "hi"}]},
    )
    assert pts == 497
