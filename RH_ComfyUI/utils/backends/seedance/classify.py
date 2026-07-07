"""Seedance 任务形态自动分类器

唯一的"按输入自动匹配任务形态"入口,被所有 Provider 共享使用。
判定顺序(命中即定):
  1. 显式 ordered_content(逐素材 role)/ params["shape"] 覆盖
  2. params["frame_mode"] 半显式覆盖(前端 API,声明在模型 schema 里):
       - "reference":  全部图片仅作参考 → MULTIMODAL(多参考生成)
       - "first_last": 强制首尾帧(图1=首帧, 图2=尾帧, 其余 reference)
       - "auto"/缺省:  走下方自动判定
  3. 含视频或音频参考 → MULTIMODAL(图/视频/音频均 reference)
  4. 图片 >= 2         → FIRST_LAST_FRAME(图1=首帧, 图2=尾帧, 其余 reference)
  5. 图片 == 1         → IMAGE2VIDEO(图1=首帧)
  6. 其余              → TEXT2VIDEO

"2 张图到底是首尾帧还是双参考"仅靠图片数量无法分辨 —— HTTP 简单调用用
frame_mode 区分;无限画布连线场景用 ordered_content 的 role 字段逐素材指定。
"""

from __future__ import annotations

import re
import hashlib
from typing import Mapping, Optional

from .spec import (
    MediaRole,
    SpecMedia,
    VideoGenSpec,
    OrderedSegment,
    VideoTaskShape,
)
from ...core.types import MediaRef, MediaKind, ContentItemType
from ...core.request import GenerationRequest

_SHAPE_OVERRIDE_MAP: dict[str, VideoTaskShape] = {
    "text2video": VideoTaskShape.TEXT2VIDEO,
    "image2video": VideoTaskShape.IMAGE2VIDEO,
    "first_last_frame": VideoTaskShape.FIRST_LAST_FRAME,
    "multimodal": VideoTaskShape.MULTIMODAL,
    "video_edit": VideoTaskShape.VIDEO_EDIT,
    "video_extend": VideoTaskShape.VIDEO_EXTEND,
}


def _shape_from_override(raw: Optional[str]) -> Optional[VideoTaskShape]:
    if not raw:
        return None
    return _SHAPE_OVERRIDE_MAP.get(raw.strip().lower())


def _next_index(media: list[SpecMedia], kind: MediaKind) -> int:
    """计算该 kind 在已收集 media 列表里下一次出现的 1-based 序号。"""
    n = 0
    for m in media:
        if m.kind == kind:
            n += 1
    return n + 1


def _media_dedup_key(ref: MediaRef) -> Optional[str]:
    """给一张图算 dedup key(同字节 → 同 key);无 data(外链)→ None 不去重。"""
    if ref.data is None or len(ref.data) == 0:
        return None
    return hashlib.sha256(ref.data).hexdigest()


# 匹配「图片N / 图片 N / image N / 视频N / 视频 N / video N / 音频N / 音频 N / audio N」,
# 捕获组 1 = digit string。IGNORECASE 处理英文大小写。
_REF_REWRITE_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"图片\s*(\d+)"), 1),
    (re.compile(r"视频\s*(\d+)"), 1),
    (re.compile(r"音频\s*(\d+)"), 1),
    (re.compile(r"image\s*(\d+)", re.IGNORECASE), 1),
    (re.compile(r"video\s*(\d+)", re.IGNORECASE), 1),
    (re.compile(r"audio\s*(\d+)", re.IGNORECASE), 1),
)


def _rewrite_image_refs(text: str, orig_to_deduped: Mapping[int, int]) -> str:
    """把 text 中「第N张图」类引用按 dedup 映射改写;映射不到的保留原样。

    例如 orig_to_deduped={3: 1, 5: 2} 时:
      - "看图片3" → "看图片1"
      - "看图片5再看图片3" → "看图片2再看图片1"
    """
    if not text or not orig_to_deduped:
        return text
    out = text

    def _sub(pattern: re.Pattern[str], grp: int, m: re.Match[str]) -> str:
        try:
            orig_idx = int(m.group(grp))
        except (TypeError, ValueError):
            return m.group(0)
        new_idx = orig_to_deduped.get(orig_idx)
        if new_idx is None or new_idx == orig_idx:
            return m.group(0)
        # 替换 N,保留前后空白样式(「图片3」→「图片1」,「图片 3」→「图片 1」)
        return m.group(0).replace(m.group(grp), str(new_idx), 1)

    for pat, grp in _REF_REWRITE_PATTERNS:
        out = pat.sub(lambda m, _p=pat, _g=grp: _sub(_p, _g, m), out)
    return out


def _build_image_dedup_map(items) -> tuple[dict[int, int], set[int]]:
    """扫一遍 ordered_content,建立每张图(按出现顺序 1-based)的 dedup 映射。

    Returns:
        (orig_to_deduped, keep_orig_positions):
          - orig_to_deduped: 原图序号 1..N → 去重后序号 1..M
          - keep_orig_positions: 哪些原图序号(1..N)要保留进 ordered_segments
    """
    seen: dict[str, int] = {}  # dedup_key → 去重后序号
    orig_to_deduped: dict[int, int] = {}
    keep: set[int] = set()
    orig_pos = 0
    dedup_pos = 0
    for item in items:
        if item.type == ContentItemType.TEXT or item.media is None:
            continue
        orig_pos += 1
        key = _media_dedup_key(item.media)
        if key is None:
            # 无 data(纯外链 URL):不去重,每个都是独立的
            dedup_pos += 1
            orig_to_deduped[orig_pos] = dedup_pos
            keep.add(orig_pos)
            continue
        cached = seen.get(key)
        if cached is not None:
            orig_to_deduped[orig_pos] = cached
            continue
        dedup_pos += 1
        seen[key] = dedup_pos
        orig_to_deduped[orig_pos] = dedup_pos
        keep.add(orig_pos)
    return orig_to_deduped, keep


def classify_video_spec(request: GenerationRequest) -> VideoGenSpec:
    """根据用户的通用输入,产出供应商无关的 `VideoGenSpec`。

    支持通过 `request.params["shape"]` 或 `ordered_content` 显式覆盖默认判定。
    """
    params = request.params or {}
    spec_params: dict[str, object] = dict(params)

    shape_override = _shape_from_override(params.get("shape") if isinstance(params.get("shape"), str) else None)

    media: list[SpecMedia] = []
    ordered_segments: list[OrderedSegment] = []
    base_prompt = request.prompt or ""

    if request.ordered_content:
        # 第一遍:算 dedup 映射(同 bytes 的图压缩成一个)
        orig_to_deduped, keep_orig = _build_image_dedup_map(request.ordered_content)

        # 第二遍:构造 ordered_segments,只保留 keep_orig 的图,text 用映射改写图片引用
        orig_pos = 0
        for item in request.ordered_content:
            if item.type == ContentItemType.TEXT:
                if item.text:
                    base_prompt = base_prompt or item.text
                    rewritten = _rewrite_image_refs(item.text, orig_to_deduped)
                    ordered_segments.append(OrderedSegment(kind="text", text=rewritten))
                continue
            if item.media is None:
                continue
            orig_pos += 1
            if orig_pos not in keep_orig:
                # 重复图:跳过(不占 content[] 位置;文本仍按位置保留)
                continue
            kind = MediaKind(item.media.kind.value)
            role = MediaRole.REFERENCE
            if item.role:
                role_norm = item.role.lower()
                if role_norm == "first_frame":
                    role = MediaRole.FIRST_FRAME
                elif role_norm == "last_frame":
                    role = MediaRole.LAST_FRAME
            idx = _next_index(media, kind)
            spec_media = SpecMedia(kind=kind, role=role, ref=item.media, index=idx)
            media.append(spec_media)
            ordered_segments.append(OrderedSegment(kind="media", media=spec_media))

    else:
        for v in request.video_refs:
            idx = _next_index(media, MediaKind.VIDEO)
            media.append(SpecMedia(kind=MediaKind.VIDEO, role=MediaRole.REFERENCE, ref=v, index=idx))
        for a in request.audio_refs:
            idx = _next_index(media, MediaKind.AUDIO)
            media.append(SpecMedia(kind=MediaKind.AUDIO, role=MediaRole.REFERENCE, ref=a, index=idx))
        for raw_img in request.images:
            from ...core.types import image_ref

            ref = image_ref(data=raw_img)
            idx = _next_index(media, MediaKind.IMAGE)
            media.append(SpecMedia(kind=MediaKind.IMAGE, role=MediaRole.REFERENCE, ref=ref, index=idx))

    n_img = len([m for m in media if m.kind == MediaKind.IMAGE])
    has_av = any(m.kind in (MediaKind.VIDEO, MediaKind.AUDIO) for m in media)

    frame_mode = str(params.get("frame_mode") or "auto").strip().lower()

    if shape_override is not None:
        shape = shape_override
    elif frame_mode == "reference":
        # 全部图片仅作参考素材(多参考生成),不指定首尾帧
        shape = VideoTaskShape.MULTIMODAL if (n_img or has_av) else VideoTaskShape.TEXT2VIDEO
    elif frame_mode == "first_last":
        # 强制首尾帧;若同时带音视频参考,形态仍是多模态,但图1/图2 的首尾帧角色保留
        shape = VideoTaskShape.MULTIMODAL if has_av else VideoTaskShape.FIRST_LAST_FRAME
    elif has_av:
        shape = VideoTaskShape.MULTIMODAL
    elif n_img >= 2:
        shape = VideoTaskShape.FIRST_LAST_FRAME
    elif n_img == 1:
        shape = VideoTaskShape.IMAGE2VIDEO
    else:
        shape = VideoTaskShape.TEXT2VIDEO

    # 媒体默认角色回填:ordered_content 已显式带 role 的尊重原值;否则按形态/frame_mode 赋值
    if not request.ordered_content:
        media = _apply_default_roles(media, shape, frame_mode)

    return VideoGenSpec(
        shape=shape,
        prompt=base_prompt,
        media=media,
        ordered_segments=ordered_segments,
        ratio=request.ratio,
        resolution=request.resolution,
        duration=request.duration or 5,
        seed=request.seed,
        generate_audio=bool(request.generate_audio),
        watermark=bool(request.watermark),
        camera_fixed=bool(request.camera_fixed),
        return_last_frame=bool(request.return_last_frame),
        service_tier=request.service_tier or "default",
        params=spec_params,
    )


def _apply_default_roles(
    media: list[SpecMedia], shape: VideoTaskShape, frame_mode: str = "auto"
) -> list[SpecMedia]:
    """未显式提供 ordered_content 时,按形态/frame_mode 回填首帧/尾帧/参考。"""
    if frame_mode == "reference":
        return media  # 全部保持 REFERENCE
    images = [m for m in media if m.kind == MediaKind.IMAGE]
    if frame_mode == "first_last" or shape == VideoTaskShape.FIRST_LAST_FRAME:
        if len(images) >= 2:
            images[0].role = MediaRole.FIRST_FRAME
            images[1].role = MediaRole.LAST_FRAME
            for m in images[2:]:
                m.role = MediaRole.REFERENCE
        elif images:
            images[0].role = MediaRole.FIRST_FRAME
    elif shape == VideoTaskShape.IMAGE2VIDEO:
        if images:
            images[0].role = MediaRole.FIRST_FRAME
    return media


__all__ = ["classify_video_spec"]
