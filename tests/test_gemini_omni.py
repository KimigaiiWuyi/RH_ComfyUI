"""Gemini Omni 视频模型(gemini_omni12):注册/参数面/计费/mapper 单测。"""

from __future__ import annotations

import base64
import asyncio

import pytest
from PIL import Image

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.utils.core.types import audio_ref, video_ref
from RH_ComfyUI.models.video.defs import GeminiOmni12Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.gemini_omni import gemini_omni_mapper
from RH_ComfyUI.utils.mappers.gemini_omni_billing import (
    settle_gemini_omni_points,
    estimate_gemini_omni_points,
)


def _png() -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _req(**kw) -> GenerationRequest:
    return GenerationRequest(model="gemini_omni12", task_type=TaskType.VIDEO, **kw)


# ── 注册与参数面 ──────────────────────────────────────────────


def test_catalog_identity():
    m = GeminiOmni12Def()
    assert m.name == "gemini_omni12"
    assert m.node.backend == "gemini-omni"
    assert m.node.backend_model == "gemini-omni-1.2-flash"
    assert m.node.requirements == ["gemini_image_apikey"]
    assert m.supports_remote_cancel is True


def test_schema_ports():
    m = GeminiOmni12Def()
    ports = m.node.inputs
    assert ports["resolution"].values == ["360p", "720p", "1080p", "4k"]
    assert ports["ratio"].values == ["16:9", "9:16"]
    dur = ports["duration"]
    assert dur.minimum == 3 and dur.maximum == 15 and dur.default == 5
    assert ports["images"].max_items == 6
    assert ports["video_refs"].max_items == 3


def test_validate_rejects_over_6_images():
    m = GeminiOmni12Def()
    with pytest.raises(ValidationError, match="6"):
        m.validate(_req(prompt="p", images=[_png()] * 7))


def test_validate_video_refs_by_duration():
    m = GeminiOmni12Def()
    v = lambda i: video_ref(data=b"\x00\x00\x00\x18ftypmp42" + bytes([i]))  # noqa: E731
    # >10s 只允许 1 段参考视频
    with pytest.raises(ValidationError, match="10s"):
        m.validate(_req(prompt="p", duration=12, video_refs=[v(1), v(2)], params={"duration": 12}))
    # ≤10s 允许 3 段
    m.validate(_req(prompt="p", duration=8, video_refs=[v(1), v(2), v(3)], params={"duration": 8}))
    # >3 段一律拒
    with pytest.raises(ValidationError, match="3"):
        m.validate(_req(prompt="p", duration=5, video_refs=[v(1), v(2), v(3), v(4)], params={"duration": 5}))


def test_validate_counts_oc_videos_too():
    """OC 里的视频项也计入上限(与 mapper 发送口径一致)。"""
    from RH_ComfyUI.core.schema.types import ContentItem, ContentItemType

    m = GeminiOmni12Def()
    v = lambda i: video_ref(data=b"\x00\x00\x00\x18ftypmp42" + bytes([i]))  # noqa: E731
    oc = [ContentItem(type=ContentItemType.VIDEO, media=v(i)) for i in range(4)]
    with pytest.raises(ValidationError, match="3"):
        m.validate(_req(prompt="p", duration=5, ordered_content=oc, params={"duration": 5}))
    # 扁平+OC 同内容副本去重后不误拒
    same = b"\x00\x00\x00\x18ftypmp42dup"
    oc_dup = [ContentItem(type=ContentItemType.VIDEO, media=video_ref(data=same))]
    m.validate(
        _req(
            prompt="p",
            duration=5,
            video_refs=[video_ref(data=same)],
            ordered_content=oc_dup,
            params={"duration": 5},
        )
    )


def test_validate_rejects_audio_refs():
    m = GeminiOmni12Def()
    with pytest.raises(ValidationError, match="音频"):
        m.validate(_req(prompt="p", audio_refs=[audio_ref(data=b"a")]))


# ── 计费(官方价:$12/M 视频输出,$1.5/M 输入;USD×7×100 → 积分) ──


def test_billing_720p_per_second():
    # 1s 720p = 5792 tok × $12/M × 700 = 48.65 分;输入固定小额另计
    pts_1s = estimate_gemini_omni_points("720p", 1)
    assert pts_1s == round(5792 * 12 * 700 / 1e6 + 400 * 1.5 * 700 / 1e6)


def test_billing_scales_with_resolution_and_duration():
    p360 = estimate_gemini_omni_points("360p", 10)
    p720 = estimate_gemini_omni_points("720p", 10)
    p4k = estimate_gemini_omni_points("4k", 10)
    assert p360 < p720 < p4k
    # 输出部分与 token 率严格成比例
    assert abs(p4k - p360 - (17376 - 1931) * 10 * 12 * 700 / 1e6) < 2


def test_settle_uses_actual_output_tokens():
    # 上游回真实输出 token → 按实结算(与估算不同的值也能算出来)
    pts = settle_gemini_omni_points({"output_tokens": 57920}, "720p")
    assert pts == round(57920 * 12 * 700 / 1e6 + 400 * 1.5 * 700 / 1e6)


def test_settle_without_usage_returns_none():
    assert settle_gemini_omni_points({}, "720p") is None


def test_point_range_monotonic():
    lo, hi = GeminiOmni12Def().point_range()
    assert 0 < lo < hi


# ── mapper:contents 组装与响应 → NodeOutput ──────────────────


class _FakeAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_video(self, **kw):
        self.calls.append(kw)
        return b"fake-mp4", {"output_tokens": 57920}


def test_mapper_builds_contents_and_response_format():
    api = _FakeAPI()
    img = _png()
    vid = b"\x00\x00\x00\x18ftypmp42xxxx"
    req = _req(
        prompt="猫在跑",
        images=[img],
        video_refs=[video_ref(data=vid)],
        ratio="9:16",
        resolution="1080p",
        duration=12,
        params={"model": "gemini-omni-1.2-flash"},
    )
    out = asyncio.run(gemini_omni_mapper(req, api))
    assert out.output_type == "video" and out.data == b"fake-mp4" and out.mime_type == "video/mp4"
    assert out.usage["output_tokens"] == 57920

    call = api.calls[0]
    assert call["model"] == "gemini-omni-1.2-flash"
    assert call["aspect_ratio"] == "9:16"
    assert call["resolution"] == "1080p"
    assert call["duration_s"] == 12
    kinds = [c["type"] for c in call["contents"]]
    assert kinds == ["image", "video", "text"]  # 媒体在前,文本在后
    assert base64.b64decode(call["contents"][0]["data"]) == img
    assert call["contents"][0]["mime_type"] == "image/png"
    assert call["contents"][1]["mime_type"] == "video/mp4"
    assert call["contents"][2]["text"] == "猫在跑"


def test_mapper_defaults_and_clamps():
    api = _FakeAPI()
    asyncio.run(gemini_omni_mapper(_req(prompt="p", duration=99), api))
    call = api.calls[0]
    assert call["aspect_ratio"] == "16:9" and call["resolution"] == "720p"
    assert call["duration_s"] == 15  # clamp 到上限


def test_mapper_dedupes_flat_and_oc_copies():
    """扁平 video_refs 与 OC 视频项同内容 → 只发一份(输入 token 不翻倍)。"""
    api = _FakeAPI()
    from RH_ComfyUI.core.schema.types import ContentItem, ContentItemType

    same = b"\x00\x00\x00\x18ftypmp42dup"
    oc = [ContentItem(type=ContentItemType.VIDEO, media=video_ref(data=same))]
    req = _req(prompt="p", video_refs=[video_ref(data=same)], ordered_content=oc)
    asyncio.run(gemini_omni_mapper(req, api))
    kinds = [c["type"] for c in api.calls[0]["contents"]]
    assert kinds == ["video", "text"]


def test_mapper_video_url_only_ref_sends_uri():
    api = _FakeAPI()
    req = _req(prompt="p", video_refs=[video_ref(url="https://x/ref.mp4")])
    asyncio.run(gemini_omni_mapper(req, api))
    video_part = api.calls[0]["contents"][0]
    assert video_part == {"type": "video", "uri": "https://x/ref.mp4"}


def test_mapper_rejects_empty_request():
    api = _FakeAPI()
    with pytest.raises(ValueError, match="空"):
        asyncio.run(gemini_omni_mapper(_req(prompt=""), api))


# ── api._find_video:steps 里提取视频 ──────────────────────────


def test_find_video_in_steps():
    from RH_ComfyUI.utils.backends.gemini_omni.api import _find_video

    video_b64 = base64.b64encode(b"mp4-bytes").decode()
    interaction = {
        "steps": [
            {"type": "user_input", "content": [{"type": "text", "text": "p"}]},
            {"type": "model_output", "content": [{"type": "video", "mime_type": "video/mp4", "data": video_b64}]},
        ]
    }
    data, uri = _find_video(interaction)
    assert data == b"mp4-bytes" and uri is None


def test_find_video_uri_fallback():
    from RH_ComfyUI.utils.backends.gemini_omni.api import _find_video

    interaction = {"steps": [{"content": [{"type": "video", "uri": "https://x/v.mp4"}]}]}
    data, uri = _find_video(interaction)
    assert data is None and uri == "https://x/v.mp4"
