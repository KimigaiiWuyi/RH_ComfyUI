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


def test_ordered_content_does_not_double_count_flat_images():
    """有序段已有图时,扁平 images 即使是同图不同 identity 形态也不得再追加。

    回归:前端 Seedance 路径同时发 ordered_content + images;旧实现 url-vs-bytes
    去重失败会把 9 张变成 18 张 → MEDIA_OVERFLOW(图≤9)。
    """
    imgs = [_img_item(bytes([i]) + b"PNG") for i in range(9)]
    # 扁平侧用同一批 bytes(模拟 RH api 解码后的 list[bytes])
    flat_bytes = [bytes([i]) + b"PNG" for i in range(9)]
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="多参考",
        ordered_content=[_txt("多参考"), *imgs],
        images=flat_bytes,
        params={"frame_mode": "reference"},
    )
    spec = classify_video_spec(req)
    assert len(spec.images()) == 9, f"应保持 9 张,实际 {len(spec.images())}"


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


def _video_item(url: str) -> ContentItem:
    return ContentItem(
        type=ContentItemType.VIDEO,
        media=MediaRef(kind=MediaKind.VIDEO, url=url),
        role="reference",
    )


def test_extend_prompt_without_token_gets_prefix():
    """task_mode=extend 且全文无「延长」→ 最前补「延长该视频。」"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="镜头继续往前推",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        params={"task_mode": "extend"},
    )
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.VIDEO_EXTEND
    assert spec.prompt.startswith("延长该视频。")
    assert spec.prompt.endswith("镜头继续往前推")


def test_extend_prompt_already_has_token_unchanged():
    """前端已写「延长该视频 @x 视频。」时后端不得再叠一层。"""
    prompt = "延长该视频 [参考视频1] 视频。镜头继续"
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=prompt,
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        params={"task_mode": "extend"},
    )
    spec = classify_video_spec(req)
    assert spec.prompt == prompt


def test_extend_ordered_content_text_gets_prefix():
    """OC 文本段同样检查并补前缀(ark 走 ordered_segments 拼 content)。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="镜头继续",
        ordered_content=[_txt("镜头继续"), _video_item("https://ex.com/v.mp4")],
        params={"task_mode": "extend"},
    )
    spec = classify_video_spec(req)
    assert spec.prompt.startswith("延长该视频。")
    assert spec.ordered_segments[0].kind == "text"
    assert spec.ordered_segments[0].text == "延长该视频。镜头继续"


def test_extend_ordered_content_media_first_inserts_text():
    """OC 以媒体开头且无「延长」→ 在最前插入文本段。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="",
        ordered_content=[_video_item("https://ex.com/v.mp4"), _txt("继续这个镜头")],
        params={"task_mode": "extend"},
    )
    spec = classify_video_spec(req)
    # 扁平 prompt 会先被 OC 文本回填成「继续这个镜头」,再叠前缀
    assert spec.prompt.startswith("延长该视频。")
    assert spec.ordered_segments[0].kind == "text"
    assert spec.ordered_segments[0].text == "延长该视频。"
    assert spec.ordered_segments[1].kind == "media"


def test_edit_prompt_without_token_not_prefixed():
    """编辑任务即使没有「延长」也不得误加延长前缀。"""
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="把背景换成海边",
        video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        params={"task_mode": "edit"},
    )
    spec = classify_video_spec(req)
    assert spec.shape == VideoTaskShape.VIDEO_EDIT
    assert spec.prompt == "把背景换成海边"
