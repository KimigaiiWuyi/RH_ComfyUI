"""Seedance 任务形态自动分类器

唯一的"按输入自动匹配任务形态"入口,被所有 Provider 共享使用。
判定顺序(命中即定):
  1. 显式 ordered_content(逐素材 role)/ params["shape"] 覆盖
  2. params["frame_mode"] 半显式覆盖(调用方 API,声明在模型 schema 里):
       - "reference":  全部图片仅作参考 → MULTIMODAL(多参考生成)
       - "first_last": 强制首尾帧(图1=首帧, 图2=尾帧, 其余 reference)
       - "auto"/缺省:  走下方自动判定
  3. 含视频或音频参考 → MULTIMODAL(图/视频/音频均 reference)
  4. 图片 >= 2         → FIRST_LAST_FRAME(图1=首帧, 图2=尾帧, 其余 reference)
  5. 图片 == 1         → IMAGE2VIDEO(图1=首帧)
  6. 其余              → TEXT2VIDEO

"2 张图到底是首尾帧还是双参考"仅靠图片数量无法分辨 —— HTTP 简单调用用
frame_mode 区分;多素材连线场景用 ordered_content 的 role 字段逐素材指定。
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


# 匹配「[参考图片N] / 图片N / image N / …」,捕获组 = digit。
# 结构化标记优先,避免 dedup 改写时只动内层数字导致括号残缺。
# IGNORECASE 处理英文大小写。
_REF_REWRITE_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\[\s*参考图片\s*(\d+)\s*\]"), 1),
    (re.compile(r"\[\s*参考视频\s*(\d+)\s*\]"), 1),
    (re.compile(r"\[\s*参考音频\s*(\d+)\s*\]"), 1),
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

    # ordered_content 仅含文本段(无任何媒体项)时不能走有序分支 —— 否则 else 分支里
    # 的 images / video_refs / audio_refs(调用方把"连线但未 @"的素材放在这些扁平字段里)
    # 会被整体忽略,导致"普通链接没 @"时图片凭空丢失。文本已由 request.prompt 承载,
    # 落到 else 分支不会丢文案。真正的有序多模态由 ordered_content 里的媒体项触发。
    oc_has_media = any(item.media is not None for item in request.ordered_content)

    if request.ordered_content and oc_has_media:
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
            # 以 MediaRef.kind 为准(构造时已按 mime/文件头纠正 video/audio 误标为 image)
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

        # 有序分支也要把"连线但未进 ordered_content"的扁平 video/audio/images
        # 追加进尾部(前端 GenerationNode 通常已写进 OC;Agent/主页部分路径
        # 会把未 @ 的素材只放在 video_refs/images 里 —— 旧逻辑会整批丢掉)。
        media, ordered_segments = _append_flat_media_not_in_ordered(media, ordered_segments, request)

    else:
        for v in request.video_refs:
            kind = MediaKind(v.kind.value)
            idx = _next_index(media, kind)
            media.append(SpecMedia(kind=kind, role=MediaRole.REFERENCE, ref=v, index=idx))
        for a in request.audio_refs:
            kind = MediaKind(a.kind.value)
            idx = _next_index(media, kind)
            media.append(SpecMedia(kind=kind, role=MediaRole.REFERENCE, ref=a, index=idx))
        for raw_img in request.images:
            from ...core.types import image_ref

            ref = image_ref(data=raw_img)
            # image_ref 构造后 __post_init__ 可能把 mp4 纠正为 VIDEO
            kind = MediaKind(ref.kind.value)
            idx = _next_index(media, kind)
            media.append(SpecMedia(kind=kind, role=MediaRole.REFERENCE, ref=ref, index=idx))

    n_img = len([m for m in media if m.kind == MediaKind.IMAGE])
    has_av = any(m.kind in (MediaKind.VIDEO, MediaKind.AUDIO) for m in media)

    frame_mode = str(params.get("frame_mode") or "auto").strip().lower()
    # task_mode: Seedance 2.5 等把「生成 / 编辑 / 延长」显式拆开的半显式开关
    # (与 frame_mode 正交;frame_mode 只管图片角色,task_mode 管任务形态)
    task_mode = str(params.get("task_mode") or "auto").strip().lower()

    if shape_override is not None:
        shape = shape_override
    elif task_mode == "edit":
        shape = VideoTaskShape.VIDEO_EDIT
    elif task_mode == "extend":
        shape = VideoTaskShape.VIDEO_EXTEND
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

    # duration=-1 是 Seedance 2.5 编辑/延长的「跟随输入」语义,必须透传,不能被 or 吃掉
    raw_duration = request.duration
    if raw_duration is None:
        duration = 5
    else:
        duration = int(raw_duration)

    output_format = params.get("output_format")
    if isinstance(output_format, str):
        output_format = output_format.strip().lower() or None
    else:
        output_format = None

    if task_mode == "extend":
        base_prompt, ordered_segments = _ensure_extend_prompt_prefix(base_prompt, ordered_segments)

    return VideoGenSpec(
        shape=shape,
        prompt=base_prompt,
        media=media,
        ordered_segments=ordered_segments,
        ratio=request.ratio,
        resolution=request.resolution,
        duration=duration,
        seed=request.seed,
        generate_audio=bool(request.generate_audio),
        watermark=bool(request.watermark),
        camera_fixed=bool(request.camera_fixed),
        return_last_frame=bool(request.return_last_frame),
        service_tier=request.service_tier or "default",
        output_format=output_format,
        omni_reference_task_type=_resolve_omni_reference_task_type(params, task_mode),
        params=spec_params,
    )


_OMNI_REF_TASK_TYPES = frozenset({"auto", "edit", "extend"})
_EXTEND_TOKEN = "延长"
_EXTEND_PREFIX = "延长该视频。"


def _resolve_omni_reference_task_type(params: dict[str, object], task_mode: str) -> str:
    """Seedance 2.5 官方 omni_reference_task_type:显式值优先,否则跟 task_mode。"""
    raw = params.get("omni_reference_task_type")
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in _OMNI_REF_TASK_TYPES:
            return v
    if task_mode in ("edit", "extend"):
        return task_mode
    return "auto"


def _collect_prompt_haystack(base_prompt: str, ordered_segments: list[OrderedSegment]) -> str:
    """整段发给上游的文案:扁平 prompt + OC 文本段。"""
    parts: list[str] = [base_prompt or ""]
    for seg in ordered_segments:
        if seg.kind == "text" and seg.text:
            parts.append(seg.text)
    return "".join(parts)


def _ensure_extend_prompt_prefix(
    base_prompt: str,
    ordered_segments: list[OrderedSegment],
) -> tuple[str, list[OrderedSegment]]:
    """task_mode=extend 兜底:整段 prompt/OC 文本都没有「延长」时,最前补「延长该视频。」

    前端提交时会写成「延长该视频 @视频 视频。」;本函数只在调用方漏写时补短前缀,
    避免无参考视频标题时硬编造 @。已含「延长」则原样返回,防止双写。
    """
    if _EXTEND_TOKEN in _collect_prompt_haystack(base_prompt, ordered_segments):
        return base_prompt, ordered_segments

    prefix = _EXTEND_PREFIX
    new_prompt = f"{prefix}{base_prompt}" if base_prompt else prefix
    if not ordered_segments:
        return new_prompt, ordered_segments

    segs = list(ordered_segments)
    first = segs[0]
    if first.kind == "text":
        segs[0] = OrderedSegment(kind="text", text=f"{prefix}{first.text or ''}")
    else:
        segs.insert(0, OrderedSegment(kind="text", text=prefix))
    return new_prompt, segs


def _apply_default_roles(media: list[SpecMedia], shape: VideoTaskShape, frame_mode: str = "auto") -> list[SpecMedia]:
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


def _media_identities(ref: MediaRef) -> list[str]:
    """同一 MediaRef 可能同时有 bytes 与 url —— 两套键都登记,避免「OC 侧 url、
    扁平侧 bytes」对不上而把同图追加两次,顶破 max_images=9。
    """
    ids: list[str] = []
    key = _media_dedup_key(ref)
    if key is not None:
        ids.append(f"data:{key}")
    url = (ref.url or "").strip()
    if url:
        ids.append(f"url:{url}")
    return ids


def _media_identity(ref: MediaRef) -> Optional[str]:
    """兼容旧调用:返回第一个可用去重键。"""
    ids = _media_identities(ref)
    return ids[0] if ids else None


def _append_flat_media_not_in_ordered(
    media: list[SpecMedia],
    ordered_segments: list[OrderedSegment],
    request: GenerationRequest,
) -> tuple[list[SpecMedia], list[OrderedSegment]]:
    """把 request 的扁平 video_refs / audio_refs / images 中尚未出现在
    ordered_content 里的素材,以 REFERENCE 角色追加到 media 与有序段尾部。

    注意:
    - 调用方可能**同时**发 ordered_content 与扁平 images(后者作兼容备份)。
      旧实现只认单一 identity,url/bytes 不一致时会把 N 张图再追加一遍 → 2N →
      顶破上游 ``max_images``。
    - 若有序段**已有图**,跳过扁平 images(有序段为权威图列表;扁平留给未写
      OC 的入口)。video/audio 仍按 identity 去重追加。
    """
    seen: set[str] = set()
    for m in media:
        for ident in _media_identities(m.ref):
            seen.add(ident)

    oc_has_image = any(m.kind == MediaKind.IMAGE for m in media)

    def _add(ref: MediaRef, kind: MediaKind) -> None:
        nonlocal media, ordered_segments
        idents = _media_identities(ref)
        if idents and any(i in seen for i in idents):
            return
        for i in idents:
            seen.add(i)
        idx = _next_index(media, kind)
        spec_media = SpecMedia(kind=kind, role=MediaRole.REFERENCE, ref=ref, index=idx)
        media.append(spec_media)
        ordered_segments.append(OrderedSegment(kind="media", media=spec_media))

    for v in request.video_refs:
        _add(v, MediaKind(v.kind.value))
    for a in request.audio_refs:
        _add(a, MediaKind(a.kind.value))

    # 有序段已有图 → 不再从扁平 images 补图(防双计)
    if not oc_has_image:
        for raw_img in request.images:
            from ...core.types import image_ref

            ref = image_ref(data=raw_img)
            _add(ref, MediaKind(ref.kind.value))
    return media, ordered_segments


__all__ = ["classify_video_spec"]
