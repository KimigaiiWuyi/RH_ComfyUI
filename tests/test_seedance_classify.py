"""classify_video_spec — 输入形态分类 / 有序内容与扁平媒体的取舍

回归重点:ordered_content **仅含文本段**(前端 buildOrderedContent 对任意 prompt
都会产出一个 text 段)时,不能走有序分支 —— 否则 else 分支里的 images /
video_refs / audio_refs("连线但未 @"的素材落在这些扁平字段)会被整体忽略,
导致"普通链接没 @"时图片凭空丢失(线上现象:gateway dreamina-seedance-2.0
的载荷 content 只剩一段 text)。
"""

from RH_ComfyUI.core.schema.types import MediaRef, MediaKind, ContentItem, ContentItemType
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.spec import VideoTaskShape
from RH_ComfyUI.utils.backends.seedance.classify import classify_video_spec


def _txt(t: str) -> ContentItem:
    return ContentItem(type=ContentItemType.TEXT, text=t)


def _img_item(data: bytes) -> ContentItem:
    return ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, data=data))


def test_text_only_ordered_content_keeps_flat_images():
    """纯文本 ordered_content + 扁平 images:图片必须保留(走 else 分支)。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="角色在跳舞",
        images=[b"DANCER"],
        ordered_content=[_txt("角色在跳舞")],
    )
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.IMAGE2VIDEO
    assert len(spec.images()) == 1  # 图片未因 text-only ordered_content 被丢弃
    assert spec.prompt == "角色在跳舞"


def test_text_only_ordered_content_pure_text_is_t2v():
    """纯文本 ordered_content 且无扁平媒体 → 文生视频,文案来自 request.prompt。"""
    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="角色在跳舞", ordered_content=[_txt("角色在跳舞")])
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.TEXT2VIDEO
    assert not spec.media
    assert spec.prompt == "角色在跳舞"


def test_ordered_content_with_media_takes_ordered_branch():
    """含媒体项的 ordered_content 仍走有序分支,保留交错顺序(未被误伤)。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="",
        ordered_content=[_txt("把"), _img_item(b"CAT"), _txt("放大")],
    )
    spec = classify_video_spec(req)
    assert len(spec.images()) == 1
    assert [s.kind for s in spec.ordered_segments] == ["text", "media", "text"]


def _mp4_bytes() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def test_ordered_content_merges_flat_video_refs():
    """有序段已有图时,扁平 video_refs 不得被丢掉(应追加为 VIDEO 参考)。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="图+视频",
        ordered_content=[_txt("图+视频"), _img_item(b"CAT")],
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        params={"frame_mode": "reference"},
    )
    spec = classify_video_spec(req)
    assert len(spec.images()) == 1
    assert len(spec.videos()) == 1
    assert spec.videos()[0].ref.url == "https://ex.com/v.mp4"
    assert spec.shape == VideoTaskShape.MULTIMODAL


def test_mp4_bytes_in_images_become_video_kind():
    """images 通道误塞 mp4 字节时,MediaRef 纠正为 VIDEO(防 content[] image_url)。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="参考",
        images=[_mp4_bytes()],
        params={"frame_mode": "reference"},
    )
    spec = classify_video_spec(req)
    assert len(spec.videos()) == 1
    assert len(spec.images()) == 0
    assert spec.shape == VideoTaskShape.MULTIMODAL
