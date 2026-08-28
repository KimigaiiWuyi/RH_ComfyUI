"""Seedance content[] 媒体 type 键回归

视频/音频误标 image_url 会触发上游 VID-4001
(The request failed because the image format is not supported)。
本文件覆盖:classify → ContentArrayMixin._build_content 全路径。
"""

from __future__ import annotations

import json
import asyncio

from RH_ComfyUI.core.schema.types import MediaRef, MediaKind, ContentItem, ContentItemType
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.classify import classify_video_spec
from RH_ComfyUI.utils.backends.seedance.providers.ark import ArkSeedanceProvider


def _mp4() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _build(req: GenerationRequest) -> list[dict]:
    spec = classify_video_spec(req)
    p = ArkSeedanceProvider(api_key="x")
    return asyncio.run(p._build_content(spec))


def test_video_ref_emits_video_url_type():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="用视频1",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        params={"frame_mode": "reference"},
    )
    content = _build(req)
    types = [c["type"] for c in content]
    assert "video_url" in types
    assert "image_url" not in types
    vid = next(c for c in content if c["type"] == "video_url")
    assert "video_url" in vid and vid["video_url"]["url"] == "https://ex.com/v.mp4"
    assert vid.get("role") == "reference_video"


def test_audio_ref_emits_audio_url_type():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="用音频1",
        audio_refs=[MediaRef(kind=MediaKind.AUDIO, url="https://ex.com/a.mp3")],
        # 音频参考通常需配图/视频;此处仅断言 content type 键
        images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 16],
        params={"frame_mode": "reference"},
    )
    content = _build(req)
    types = {c["type"] for c in content}
    assert "audio_url" in types
    assert "image_url" in types
    aud = next(c for c in content if c["type"] == "audio_url")
    assert aud.get("role") == "reference_audio"


def test_mp4_mislabeled_as_image_emits_video_url():
    """mp4 字节误入 images → content[] 仍必须是 video_url,绝不能 image_url。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="参考",
        images=[_mp4()],
        params={"frame_mode": "reference"},
    )
    content = _build(req)
    # 脱敏 data: 体积
    compact = json.loads(json.dumps(content, default=str))
    types = [c["type"] for c in compact if c["type"] != "text"]
    assert types == ["video_url"], compact
    assert "image_url" not in {c["type"] for c in compact}


def test_ordered_content_keeps_mention_order_not_kind_groups():
    """@图 @视频 @图 → content[] 必须是 image, video, image, 不能打散成先图后视频。

    网关报 content[2] 时要能对上第 2 个 @（视频），不能变成第 2 张图。
    """
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="",
        ordered_content=[
            ContentItem(type=ContentItemType.TEXT, text="先图后视频再图"),
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, url="https://ex.com/a.png"),
                role="reference",
            ),
            ContentItem(
                type=ContentItemType.VIDEO,
                media=MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4"),
                role="reference",
            ),
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, url="https://ex.com/c.png"),
                role="reference",
            ),
        ],
        params={"frame_mode": "reference"},
    )
    content = _build(req)
    types = [c["type"] for c in content]
    assert types == ["text", "image_url", "video_url", "image_url"], content
    text = str(content[0].get("text") or "")
    assert text.index("[@参考图片1]") < text.index("[@参考视频1]") < text.index("[@参考图片2]")
    assert content[2]["video_url"]["url"] == "https://ex.com/v.mp4"


def test_ordered_content_video_url_preserved():
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="",
        ordered_content=[
            ContentItem(type=ContentItemType.TEXT, text="用视频1"),
            ContentItem(
                type=ContentItemType.VIDEO,
                media=MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4"),
                role="reference",
            ),
        ],
        params={"frame_mode": "reference"},
    )
    content = _build(req)
    assert any(c["type"] == "video_url" for c in content)
    assert not any(c["type"] == "image_url" for c in content)
