"""万相 3.0 任务形态分类

对齐 Seedance 2.0:
  1. params["shape"] 显式覆盖
  2. frame_mode=reference → MULTIMODAL
  3. frame_mode=first_last → FIRST_LAST_FRAME
  4. 含视频 / 音频 / 参考文件 / 网页 → MULTIMODAL
  5. 图片 >= 2 → FIRST_LAST_FRAME
  6. 图片 == 1 → IMAGE2VIDEO
  7. 其余 → TEXT2VIDEO

file_url / link_url 走全能参考,与 first_frame / last_frame 互斥。
"""

from __future__ import annotations

import re
from typing import Optional

from ...core.request import GenerationRequest
from ..seedance.spec import MediaRole, VideoGenSpec, VideoTaskShape
from ..seedance.classify import classify_video_spec
from ..happyhorse.classify import to_api_resolution

VENDOR_MODEL = "wan3.0-video"

# 图片N / [Image N] / [@参考图片N] / [参考图片N] → 图N(官方 prompt 指代)
_IMAGE_REF_PATTERN = re.compile(
    r"\[\s*@?\s*参考图片\s*(\d+)\s*\]"
    r"|图片\s*(\d+)"
    r"|\[\s*[Ii]mage\s*(\d+)\s*\]"
    r"|(?<![A-Za-z\u4e00-\u9fff])[Ii]mage\s*(\d+)"
)
_VIDEO_REF_PATTERN = re.compile(r"\[\s*@?\s*参考视频\s*(\d+)\s*\]")
_AUDIO_REF_PATTERN = re.compile(r"\[\s*@?\s*参考音频\s*(\d+)\s*\]")


def _param_url(params: dict, *keys: str) -> str:
    for key in keys:
        raw = params.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def file_url_of(params: dict) -> str:
    return _param_url(params, "file_url", "file")


def link_url_of(params: dict) -> str:
    return _param_url(params, "link_url", "link")


def has_file_or_link(params: dict) -> bool:
    return bool(file_url_of(params) or link_url_of(params))


def classify_wan30(request: GenerationRequest) -> VideoGenSpec:
    """在 Seedance 2.0 分类之上叠参考文件 / 网页。"""
    spec = classify_video_spec(request)
    spec.params = dict(request.params or {})

    n_img = len(spec.images())
    has_av = bool(spec.videos() or spec.audios())
    has_doc = has_file_or_link(spec.params)
    frame_mode = str(spec.params.get("frame_mode") or "auto").strip().lower()

    shape_raw = spec.params.get("shape")
    shape_override: Optional[VideoTaskShape] = None
    if isinstance(shape_raw, str) and shape_raw.strip():
        try:
            shape_override = VideoTaskShape(shape_raw.strip().lower())
        except ValueError:
            shape_override = None

    if shape_override is not None:
        shape = shape_override
    elif frame_mode == "reference":
        shape = VideoTaskShape.MULTIMODAL if (n_img or has_av or has_doc) else VideoTaskShape.TEXT2VIDEO
    elif frame_mode == "first_last":
        shape = VideoTaskShape.FIRST_LAST_FRAME if n_img >= 2 else VideoTaskShape.IMAGE2VIDEO
    elif has_av or has_doc:
        shape = VideoTaskShape.MULTIMODAL
    elif n_img >= 3:
        shape = VideoTaskShape.MULTIMODAL
    elif n_img == 2:
        shape = VideoTaskShape.FIRST_LAST_FRAME
    elif n_img == 1:
        shape = VideoTaskShape.IMAGE2VIDEO
    else:
        shape = VideoTaskShape.TEXT2VIDEO

    spec.shape = shape
    _apply_wan30_roles(spec, frame_mode=frame_mode)
    return spec


def _apply_wan30_roles(spec: VideoGenSpec, *, frame_mode: str) -> None:
    images = spec.images()
    if spec.shape == VideoTaskShape.IMAGE2VIDEO:
        if images:
            images[0].role = MediaRole.FIRST_FRAME
            for extra in images[1:]:
                extra.role = MediaRole.REFERENCE
    elif spec.shape == VideoTaskShape.FIRST_LAST_FRAME:
        if images:
            images[0].role = MediaRole.FIRST_FRAME
        if len(images) >= 2:
            images[1].role = MediaRole.LAST_FRAME
        for extra in images[2:]:
            extra.role = MediaRole.REFERENCE
    else:
        for img in images:
            img.role = MediaRole.REFERENCE
        for vid in spec.videos():
            vid.role = MediaRole.REFERENCE
        for aud in spec.audios():
            aud.role = MediaRole.REFERENCE
        _ = frame_mode


def rewrite_prompt_for_wan30(prompt: str) -> str:
    """通用「图片N」/「[Image N]」改写为官方「图N」。"""
    if not prompt:
        return prompt

    def _img(m: re.Match[str]) -> str:
        n = next(g for g in m.groups() if g is not None)
        return f"图{n}"

    out = _IMAGE_REF_PATTERN.sub(_img, prompt)
    out = _VIDEO_REF_PATTERN.sub(lambda m: f"视频{m.group(1)}", out)
    out = _AUDIO_REF_PATTERN.sub(lambda m: f"音频{m.group(1)}", out)
    return out


__all__ = [
    "VENDOR_MODEL",
    "classify_wan30",
    "file_url_of",
    "has_file_or_link",
    "link_url_of",
    "rewrite_prompt_for_wan30",
    "to_api_resolution",
]
