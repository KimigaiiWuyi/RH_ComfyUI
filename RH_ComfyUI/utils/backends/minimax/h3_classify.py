"""MiniMax H3 任务形态分类

官方四种生成模式(video-generation.md,与 Seedance 2.0/2.5 同构):
  1. 文生视频 t2va          — 仅 text;ratio 必填且不能 adaptive
  2. 图生视频 i2va          — text + 1 张 first_frame 或 last_frame
  3. 首尾帧 first_last      — text + first_frame + last_frame
  4. 全能参考 r2va          — text + reference_image/video/audio

图生(first/last)与参考(reference_*)互斥。

判定优先级:
  1. params["shape"] 显式覆盖
  2. task_mode(t2v / i2v / first_last / reference) — 与 Seedance 2.5 task_mode 同级
  3. frame_mode(first_frame / last_frame / first_last / reference)
  4. 自动:0 图=文生 / 1 图=图生首帧 / 2 图=首尾帧 / 图+音视频或≥3 图=参考
"""

from __future__ import annotations

from typing import Optional

from ...core.request import GenerationRequest
from ..seedance.spec import MediaRole, VideoGenSpec, OrderedSegment, VideoTaskShape
from ..seedance.classify import classify_video_spec

# 对外 task_mode 取值(auto 走输入自动)
TASK_MODE_T2V = "t2v"
TASK_MODE_I2V = "i2v"
TASK_MODE_FIRST_LAST = "first_last"
TASK_MODE_REFERENCE = "reference"

_TASK_MODE_ALIASES: dict[str, str] = {
    "auto": "auto",
    "generate": "auto",
    "t2v": TASK_MODE_T2V,
    "t2va": TASK_MODE_T2V,
    "text2video": TASK_MODE_T2V,
    "i2v": TASK_MODE_I2V,
    "i2va": TASK_MODE_I2V,
    "image2video": TASK_MODE_I2V,
    "first_frame": TASK_MODE_I2V,
    "first_last": TASK_MODE_FIRST_LAST,
    "first_last_frame": TASK_MODE_FIRST_LAST,
    "start_end": TASK_MODE_FIRST_LAST,
    "reference": TASK_MODE_REFERENCE,
    "r2v": TASK_MODE_REFERENCE,
    "r2va": TASK_MODE_REFERENCE,
    "multimodal": TASK_MODE_REFERENCE,
}


def normalize_task_mode(raw: Optional[str]) -> str:
    """空 / 未知 → auto;其余映射到四个官方模式之一。"""
    key = (raw or "auto").strip().lower()
    return _TASK_MODE_ALIASES.get(key, "auto")


def classify_minimax_h3(request: GenerationRequest) -> VideoGenSpec:
    """收集媒体后按 H3 四模式回填 shape / role。"""
    spec = classify_video_spec(request)
    spec.params = dict(request.params or {})

    n_img = len(spec.images())
    has_av = bool(spec.videos() or spec.audios())
    frame_mode = str(spec.params.get("frame_mode") or "auto").strip().lower()
    task_mode = normalize_task_mode(
        spec.params.get("task_mode") if isinstance(spec.params.get("task_mode"), str) else None
    )

    shape_raw = spec.params.get("shape")
    shape_override: Optional[VideoTaskShape] = None
    if isinstance(shape_raw, str) and shape_raw.strip():
        try:
            shape_override = VideoTaskShape(shape_raw.strip().lower())
        except ValueError:
            shape_override = None

    if shape_override is not None:
        shape = shape_override
    elif task_mode == TASK_MODE_T2V:
        shape = VideoTaskShape.TEXT2VIDEO
    elif task_mode == TASK_MODE_I2V:
        shape = VideoTaskShape.IMAGE2VIDEO
    elif task_mode == TASK_MODE_FIRST_LAST:
        shape = VideoTaskShape.FIRST_LAST_FRAME if n_img >= 2 else VideoTaskShape.IMAGE2VIDEO
    elif task_mode == TASK_MODE_REFERENCE:
        shape = VideoTaskShape.MULTIMODAL if (n_img or has_av) else VideoTaskShape.TEXT2VIDEO
    elif frame_mode == "reference":
        shape = VideoTaskShape.MULTIMODAL if (n_img or has_av) else VideoTaskShape.TEXT2VIDEO
    elif frame_mode == "first_last":
        shape = VideoTaskShape.FIRST_LAST_FRAME if n_img >= 2 else VideoTaskShape.IMAGE2VIDEO
    elif frame_mode in ("first_frame", "last_frame"):
        shape = VideoTaskShape.IMAGE2VIDEO
    elif has_av:
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
    spec.params["task_mode"] = task_mode
    spec.params["frame_mode"] = frame_mode
    if request.ordered_content and not spec.ordered_segments:
        spec.ordered_segments = _text_only_segments(request)
    _apply_h3_roles(spec, frame_mode=frame_mode)
    return spec


def _text_only_segments(request: GenerationRequest) -> list[OrderedSegment]:
    from ...core.types import ContentItemType

    segs: list[OrderedSegment] = []
    for item in request.ordered_content:
        if item.type == ContentItemType.TEXT and item.text:
            segs.append(OrderedSegment(kind="text", text=item.text))
    return segs


def _apply_h3_roles(spec: VideoGenSpec, *, frame_mode: str) -> None:
    images = spec.images()
    use_last = frame_mode == "last_frame"
    if spec.shape == VideoTaskShape.IMAGE2VIDEO:
        if images:
            images[0].role = MediaRole.LAST_FRAME if use_last else MediaRole.FIRST_FRAME
            # 图生只消费 1 张;多余图不写进 first/last,避免误走首尾帧
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


def to_api_resolution(resolution: Optional[str]) -> str:
    """内部 768p/2k → 官方 768P/2K。"""
    raw = (resolution or "2k").strip()
    low = raw.lower()
    if low in ("768p", "768"):
        return "768P"
    if low in ("2k", "2K"):
        return "2K"
    if raw in ("768P", "2K"):
        return raw
    return "2K"


def to_api_ratio(spec: VideoGenSpec) -> Optional[str]:
    """按形态决定发给上游的 ratio。

    - 文生:必须具体比例(缺省 16:9,拒绝 adaptive)
    - 图生首尾帧:恒 adaptive
    - 多模态参考:可选,默认 adaptive
    """
    ratio = (spec.ratio or "").strip() or None
    if spec.shape == VideoTaskShape.TEXT2VIDEO:
        if not ratio or ratio.lower() == "adaptive":
            return "16:9"
        return ratio
    if spec.shape in (VideoTaskShape.IMAGE2VIDEO, VideoTaskShape.FIRST_LAST_FRAME):
        return "adaptive"
    return ratio or "adaptive"


__all__ = [
    "TASK_MODE_T2V",
    "TASK_MODE_I2V",
    "TASK_MODE_FIRST_LAST",
    "TASK_MODE_REFERENCE",
    "normalize_task_mode",
    "classify_minimax_h3",
    "to_api_resolution",
    "to_api_ratio",
]
