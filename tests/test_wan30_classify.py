"""万相 3.0 形态分类:对齐 Seedance 2.0 + 参考文件。"""

from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.spec import MediaRole, VideoTaskShape
from RH_ComfyUI.utils.backends.wan30.classify import (
    classify_wan30,
    rewrite_prompt_for_wan30,
)


def _req(**kwargs) -> GenerationRequest:
    kwargs.setdefault("task_type", TaskType.VIDEO)
    kwargs.setdefault("prompt", "测试")
    return GenerationRequest(**kwargs)


def test_t2v_zero_images():
    spec = classify_wan30(_req(images=[]))
    assert spec.shape == VideoTaskShape.TEXT2VIDEO


def test_i2v_one_image():
    spec = classify_wan30(_req(images=[b"IMG"]))
    assert spec.shape == VideoTaskShape.IMAGE2VIDEO
    assert spec.images()[0].role == MediaRole.FIRST_FRAME


def test_two_images_are_first_last_like_seedance2():
    spec = classify_wan30(_req(images=[b"A", b"B"]))
    assert spec.shape == VideoTaskShape.FIRST_LAST_FRAME
    assert spec.images()[0].role == MediaRole.FIRST_FRAME
    assert spec.images()[1].role == MediaRole.LAST_FRAME


def test_frame_mode_reference_forces_multimodal():
    spec = classify_wan30(_req(images=[b"A", b"B"], params={"frame_mode": "reference"}))
    assert spec.shape == VideoTaskShape.MULTIMODAL
    assert all(m.role == MediaRole.REFERENCE for m in spec.images())


def test_video_plus_image_is_multimodal():
    spec = classify_wan30(
        _req(
            images=[b"A"],
            video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        )
    )
    assert spec.shape == VideoTaskShape.MULTIMODAL


def test_file_url_is_multimodal():
    spec = classify_wan30(_req(params={"file_url": "https://ex.com/spec.pdf"}))
    assert spec.shape == VideoTaskShape.MULTIMODAL


def test_link_url_is_multimodal():
    spec = classify_wan30(_req(params={"link_url": "https://ex.com/article"}))
    assert spec.shape == VideoTaskShape.MULTIMODAL


def test_file_url_overrides_two_image_first_last():
    spec = classify_wan30(
        _req(
            images=[b"A", b"B"],
            params={"file_url": "https://ex.com/deck.pptx"},
        )
    )
    assert spec.shape == VideoTaskShape.MULTIMODAL
    assert all(m.role == MediaRole.REFERENCE for m in spec.images())


def test_rewrite_prompt_uses_tu_n():
    assert rewrite_prompt_for_wan30("图片1抱着图片2") == "图1抱着图2"
    assert rewrite_prompt_for_wan30("[Image 1]走过[参考视频1]") == "图1走过视频1"
