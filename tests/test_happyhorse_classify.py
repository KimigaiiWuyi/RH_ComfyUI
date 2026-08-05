"""HappyHorse 形态分类与 vendor model 解析"""

from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.spec import MediaRole, VideoTaskShape
from RH_ComfyUI.utils.backends.happyhorse.classify import (
    VENDOR_MODEL_T2V,
    VENDOR_MODEL_I2V,
    VENDOR_MODEL_R2V,
    VENDOR_MODEL_EDIT,
    classify_happyhorse,
    resolve_vendor_model,
    rewrite_prompt_for_r2v,
    to_api_resolution,
)


def _req(**kwargs) -> GenerationRequest:
    kwargs.setdefault("task_type", TaskType.VIDEO)
    kwargs.setdefault("prompt", "测试")
    return GenerationRequest(**kwargs)


def test_t2v_zero_images():
    spec = classify_happyhorse(_req(images=[]))
    assert spec.shape == VideoTaskShape.TEXT2VIDEO
    assert resolve_vendor_model(spec.shape) == VENDOR_MODEL_T2V


def test_i2v_one_image():
    spec = classify_happyhorse(_req(images=[b"IMG"]))
    assert spec.shape == VideoTaskShape.IMAGE2VIDEO
    assert spec.images()[0].role == MediaRole.FIRST_FRAME
    assert resolve_vendor_model(spec.shape) == VENDOR_MODEL_I2V


def test_r2v_two_images_not_first_last():
    """HappyHorse 无首尾帧端点:2 张图走 r2v,而非 first_last。"""
    spec = classify_happyhorse(_req(images=[b"A", b"B"]))
    assert spec.shape == VideoTaskShape.MULTIMODAL
    assert all(m.role == MediaRole.REFERENCE for m in spec.images())
    assert resolve_vendor_model(spec.shape) == VENDOR_MODEL_R2V


def test_frame_mode_reference_one_image():
    spec = classify_happyhorse(_req(images=[b"ONLY"], params={"frame_mode": "reference"}))
    assert spec.shape == VideoTaskShape.MULTIMODAL
    assert resolve_vendor_model(spec.shape) == VENDOR_MODEL_R2V


def test_frame_mode_first_frame():
    spec = classify_happyhorse(_req(images=[b"A", b"B"], params={"frame_mode": "first_frame"}))
    assert spec.shape == VideoTaskShape.IMAGE2VIDEO
    assert spec.images()[0].role == MediaRole.FIRST_FRAME


def test_video_edit():
    spec = classify_happyhorse(
        _req(
            images=[b"REF"],
            video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")],
        )
    )
    assert spec.shape == VideoTaskShape.VIDEO_EDIT
    assert resolve_vendor_model(spec.shape) == VENDOR_MODEL_EDIT


def test_rewrite_prompt_image_refs():
    assert rewrite_prompt_for_r2v("图片1中的女孩与图片 2") == "[Image 1]中的女孩与[Image 2]"
    assert rewrite_prompt_for_r2v("看[参考图片1]和[参考图片2]") == "看[Image 1]和[Image 2]"
    assert rewrite_prompt_for_r2v("看 image 1 和 Image2") == "看 [Image 1] 和 [Image 2]"
    assert rewrite_prompt_for_r2v("已是 [Image 3]") == "已是 [Image 3]"


def test_to_api_resolution():
    assert to_api_resolution("720p") == "720P"
    assert to_api_resolution("1080P") == "1080P"
    assert to_api_resolution("480p") == "480P"
    assert to_api_resolution(None) is None
