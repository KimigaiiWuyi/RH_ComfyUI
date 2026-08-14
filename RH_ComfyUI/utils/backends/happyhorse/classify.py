"""HappyHorse 任务形态分类 + 供应商 model ID 解析

与 Seedance 差异:
- 无首尾帧专用端点:2 张及以上图一律走 r2v(参考生视频)
- 1 张图默认 i2v(first_frame);frame_mode=reference 时走 r2v
- 视频编辑必须显式 task_mode/frame_mode=edit → happyhorse-1.1-video-edit
- 仅有视频、未选编辑 → r2v 多参考(视频当参考素材)
- 不支持参考音频

判定顺序:
  1. params["shape"] 显式覆盖
  2. task_mode/frame_mode=edit → VIDEO_EDIT
  3. frame_mode=reference 且有图 → MULTIMODAL(r2v)
  4. frame_mode=first_frame/first_last 且有图 → IMAGE2VIDEO
  5. 图片数 == 1 → IMAGE2VIDEO
  6. 图片数 >= 2 → MULTIMODAL(r2v)
  7. 其余 → TEXT2VIDEO
  非编辑模式的视频不参与形态判定(r2v 只收 reference_image)。
"""

from __future__ import annotations

import re
from typing import Optional

from ...core.request import GenerationRequest
from ..seedance.spec import MediaRole, VideoGenSpec, VideoTaskShape
from ..seedance.classify import classify_video_spec

# ── 供应商侧 model ID(对外统一 happyhorse1.1,内部按形态切) ──

VENDOR_MODEL_T2V = "happyhorse-1.1-t2v"
VENDOR_MODEL_I2V = "happyhorse-1.1-i2v"
VENDOR_MODEL_R2V = "happyhorse-1.1-r2v"
VENDOR_MODEL_EDIT = "happyhorse-1.1-video-edit"

_SHAPE_TO_VENDOR: dict[VideoTaskShape, str] = {
    VideoTaskShape.TEXT2VIDEO: VENDOR_MODEL_T2V,
    VideoTaskShape.IMAGE2VIDEO: VENDOR_MODEL_I2V,
    VideoTaskShape.MULTIMODAL: VENDOR_MODEL_R2V,
    VideoTaskShape.FIRST_LAST_FRAME: VENDOR_MODEL_R2V,  # HappyHorse 无首尾帧,降级 r2v
    VideoTaskShape.VIDEO_EDIT: VENDOR_MODEL_EDIT,
    VideoTaskShape.VIDEO_EXTEND: VENDOR_MODEL_EDIT,
}

# 单次扫描:「[参考图片N]」/「图片N」/「image N」/「[Image N]」→「[Image N]」
# 结构化「[参考图片N]」必须排在裸「图片N」前,避免拆成「[参考[Image N]]」。
# 交替一次替换,避免先改成 [Image N] 再被二次包成 [[Image N]]
_IMAGE_REF_PATTERN = re.compile(
    r"\[\s*参考图片\s*(\d+)\s*\]"
    r"|图片\s*(\d+)"
    r"|\[\s*[Ii]mage\s*(\d+)\s*\]"
    r"|(?<![A-Za-z])[Ii]mage\s*(\d+)"
)


def resolve_vendor_model(shape: VideoTaskShape, *, override: Optional[str] = None) -> str:
    """形态 → 供应商 model 字段。override 非空时直接采用(外部通道注入用)。"""
    if override:
        return override
    return _SHAPE_TO_VENDOR.get(shape, VENDOR_MODEL_T2V)


def classify_happyhorse(request: GenerationRequest) -> VideoGenSpec:
    """在 Seedance 通用分类之上,按 HappyHorse 语义重映射形态与角色。

    复用 classify_video_spec 收集媒体/有序段,再覆盖 shape 与 image roles,
    避免重复实现 media 收集与 dedup。
    """
    # 先走通用分类收集 media;frame_mode=reference 时已把图标为 REFERENCE
    # 但 2 图默认 FIRST_LAST 不符合 HappyHorse,下面统一重判。
    force_ref = str((request.params or {}).get("frame_mode") or "auto").strip().lower() == "reference"
    # 临时把 frame_mode 置 reference 再分类,避免 Seedance 默认把 2 图判成首尾帧
    # 后我们还得再改 role;这里直接用 seedance 收集能力,shape 自己算。
    patched = request
    if not force_ref:
        # 复制 params,避免污染调用方
        from copy import copy

        patched = copy(request)
        patched.params = dict(request.params or {})
        # 用 reference 让 seedance 不把 2 图标成 first/last;我们后面再按需改
        if patched.params.get("frame_mode") not in ("first_frame", "first_last"):
            patched.params["frame_mode"] = "reference"

    spec = classify_video_spec(patched)
    # 还原调用方原始 params(classify 会把 params 拷进 spec.params)
    spec.params = dict(request.params or {})

    n_img = len(spec.images())
    frame_mode = str(spec.params.get("frame_mode") or "auto").strip().lower()
    task_mode = str(spec.params.get("task_mode") or "auto").strip().lower()
    explicit_edit = task_mode == "edit" or frame_mode == "edit"

    # 显式 shape 覆盖
    shape_raw = spec.params.get("shape")
    shape_override: Optional[VideoTaskShape] = None
    if isinstance(shape_raw, str) and shape_raw.strip():
        try:
            shape_override = VideoTaskShape(shape_raw.strip().lower())
        except ValueError:
            shape_override = None

    if shape_override is not None:
        shape = shape_override
    elif explicit_edit:
        shape = VideoTaskShape.VIDEO_EDIT
    elif frame_mode in ("reference",) and n_img >= 1:
        shape = VideoTaskShape.MULTIMODAL
    elif frame_mode in ("first_frame", "first_last") and n_img >= 1:
        # first_last 无对应端点:仅第 1 张作首帧,其余丢弃提示在 validate
        shape = VideoTaskShape.IMAGE2VIDEO
    elif n_img >= 2:
        shape = VideoTaskShape.MULTIMODAL
    elif n_img == 1:
        shape = VideoTaskShape.IMAGE2VIDEO
    else:
        shape = VideoTaskShape.TEXT2VIDEO

    spec.shape = shape
    _apply_happyhorse_roles(spec)
    return spec


def _apply_happyhorse_roles(spec: VideoGenSpec) -> None:
    """按最终形态回填 media role,供 render 写 type=first_frame / reference_image / video。"""
    images = spec.images()
    videos = spec.videos()
    if spec.shape == VideoTaskShape.IMAGE2VIDEO:
        if images:
            images[0].role = MediaRole.FIRST_FRAME
            for m in images[1:]:
                m.role = MediaRole.REFERENCE
    elif spec.shape in (VideoTaskShape.MULTIMODAL, VideoTaskShape.FIRST_LAST_FRAME):
        for m in images:
            m.role = MediaRole.REFERENCE
    elif spec.shape == VideoTaskShape.VIDEO_EDIT:
        for m in images:
            m.role = MediaRole.REFERENCE
        for m in videos:
            m.role = MediaRole.REFERENCE
    # T2V: 无媒体


def rewrite_prompt_for_r2v(prompt: str) -> str:
    """把通用「[参考图片N]」/「图片N」引用改写为 HappyHorse R2V 的 ``[Image N]``。"""
    if not prompt:
        return prompt

    def _sub(m: re.Match[str]) -> str:
        n = next(g for g in m.groups() if g is not None)
        return f"[Image {n}]"

    return _IMAGE_REF_PATTERN.sub(_sub, prompt)


def to_api_resolution(resolution: Optional[str]) -> Optional[str]:
    """内部 ``720p`` → DashScope ``720P``。"""
    if not resolution:
        return None
    r = resolution.strip()
    if not r:
        return None
    # 已是 720P / 1080P
    if r[-1:].upper() == "P" and r[:-1].isdigit():
        return r.upper() if r.endswith(("p", "P")) else r
    low = r.lower()
    if low.endswith("p") and low[:-1].isdigit():
        return f"{low[:-1]}P"
    if low in ("4k",):
        return "1080P"  # HappyHorse 无 4K,回落 1080P
    return r.upper()


__all__ = [
    "VENDOR_MODEL_T2V",
    "VENDOR_MODEL_I2V",
    "VENDOR_MODEL_R2V",
    "VENDOR_MODEL_EDIT",
    "classify_happyhorse",
    "resolve_vendor_model",
    "rewrite_prompt_for_r2v",
    "to_api_resolution",
]
