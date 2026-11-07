"""fal bytedance/seedance-2.0 与 seedance-2.5 请求组装。

插件已有模型 seedance2 / seedance2.5 挂到 fal 通道:
- 模式:0 素材→文生;1~2 图→图生(首尾帧);其余(≥3 图 / 带音视频参考 /
  frame_mode=reference)→参考端点。
- 2.5 的 task_mode=edit/extend → 参考端点 task=editing/extension。
- fal 表达不了的:watermark / return_last_frame / draft_task_id /
  output_format=mov / i2v 上的显式首尾帧超过 2 图 → ValidationError(换源)。
"""

from __future__ import annotations

from decimal import Decimal

from .catalog import (
    APP_PREFIXES,
    SEEDANCE_FAL_RATIOS,
    SEEDANCE_FAL_DURATION,
    SEEDANCE_FAL_REF_CAPS,
    SEEDANCE_FAL_ENDPOINTS,
    SEEDANCE_FAL_RESOLUTIONS,
)
from .payload import FalSubmit, JsonValue, _label, _text_param
from .payload_h3 import _Media, _Inputs, _inputs, _bool_param
from ...core.types import MediaKind
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

_TASK_MODES_25 = ("auto", "edit", "extend")
_OMNI_TYPES = ("reference", "edit", "extend")
_FRAME_MODES = ("auto", "first_last", "reference")
_FRAME_ROLES = {"first_frame", "last_frame"}
# 插件 edit/extend → fal task 名
_FAL_TASK = {"edit": "editing", "extend": "extension", "reference": "reference", "auto": "auto"}


def _text_choice(request: GenerationRequest, key: str, allowed: tuple[str, ...], default: str, *, label: str) -> str:
    val = _text_param(request, key, label)
    if not val:
        return default
    if val not in allowed:
        raise ValidationError(f"{label} 的 {key} 非法:{val!r};可选值:{', '.join(allowed)}")
    return val


def _duration_str(request: GenerationRequest, model_name: str, *, label: str) -> str:
    """双轨时长 → fal 枚举字符串;-1 / "auto" → "auto",整数 → "N"。"""
    lo, hi = SEEDANCE_FAL_DURATION[model_name]
    raw: object = request.duration
    if raw is None:
        raw = request.params["duration"] if "duration" in request.params else 5
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text == "auto":
            return "auto"
        if text.lstrip("-").isdigit():
            raw = int(text)
        else:
            raise ValidationError(f"{label} 的时长非法:{raw!r}")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValidationError(f"{label} 的时长必须是整数秒或 auto,实际收到 {raw!r}")
    if raw == -1:
        if model_name != "seedance2.5":
            raise ValidationError(f"{label} 的时长 -1(自动)在 fal 通道的 2.0 不支持")
        return "auto"
    if raw < lo or raw > hi:
        raise ValidationError(f"{label} 的时长 {raw}s 超出 fal 通道支持范围 {lo}~{hi}s")
    return str(raw)


def _resolution(request: GenerationRequest, model_name: str, *, label: str) -> str:
    raw = _text_param(request, "resolution", label).lower()
    if not raw and isinstance(request.resolution, str):
        raw = request.resolution.strip().lower()
    if not raw:
        return "720p"
    allowed = SEEDANCE_FAL_RESOLUTIONS[model_name]
    if raw not in allowed:
        raise ValidationError(f"{label} 在 fal 通道只支持 {'/'.join(allowed)},实际收到 {raw!r}")
    return raw


def _ratio(request: GenerationRequest, *, label: str) -> str:
    raw = _text_param(request, "ratio", label).lower()
    if not raw and isinstance(request.ratio, str):
        raw = request.ratio.strip().lower()
    if not raw:
        return "auto"
    if raw == "adaptive":
        return "auto"
    if raw not in SEEDANCE_FAL_RATIOS:
        raise ValidationError(f"{label} 的宽高比非法:{raw!r}")
    return raw


def _frame_roles(inputs: _Inputs, frame_mode: str, *, label: str) -> dict[str, _Media]:
    """OC role / flat frame_mode → 首/尾帧;非图片帧位报错。"""
    for m in inputs.media:
        if m.role in _FRAME_ROLES and m.kind != MediaKind.IMAGE:
            raise ValidationError(f"{label} 的 {m.role} 帧位只能是图片,实际收到 {m.kind.value}")
    imgs = [m for m in inputs.media if m.kind == MediaKind.IMAGE]
    frames: dict[str, _Media] = {}
    if inputs.oc_order:
        for m in imgs:
            if m.role in _FRAME_ROLES:
                if m.role in frames:
                    raise ValidationError(f"{label} 的 {m.role} 只能有一张")
                frames[m.role] = m
        return frames
    if frame_mode == "first_last" or (frame_mode == "auto" and 1 <= len(imgs) <= 2):
        if imgs:
            frames["first_frame"] = imgs[0]
        if len(imgs) > 1:
            frames["last_frame"] = imgs[1]
    return frames


def _ref_buckets(
    inputs: _Inputs, model_name: str, *, label: str
) -> tuple[list[JsonValue], list[JsonValue], list[JsonValue]]:
    imgs: list[JsonValue] = [m.uri for m in inputs.media if m.kind == MediaKind.IMAGE]
    vids: list[JsonValue] = [m.uri for m in inputs.media if m.kind == MediaKind.VIDEO]
    auds: list[JsonValue] = [m.uri for m in inputs.media if m.kind == MediaKind.AUDIO]
    cap_img, cap_vid, cap_aud, cap_total = SEEDANCE_FAL_REF_CAPS[model_name]
    if len(imgs) > cap_img:
        raise ValidationError(f"{label} 参考图最多 {cap_img} 张,实际收到 {len(imgs)}")
    if len(vids) > cap_vid:
        raise ValidationError(f"{label} 参考视频最多 {cap_vid} 段,实际收到 {len(vids)}")
    if len(auds) > cap_aud:
        raise ValidationError(f"{label} 参考音频最多 {cap_aud} 段,实际收到 {len(auds)}")
    if len(imgs) + len(vids) + len(auds) > cap_total:
        raise ValidationError(f"{label} 参考素材共 {len(imgs) + len(vids) + len(auds)} 个,超过上限 {cap_total} 个")
    return imgs, vids, auds


def _reject_unsupported(request: GenerationRequest, model_name: str, *, label: str) -> None:
    """插件参数面上 fal 表达不了的能力(换源,不静默丢)。"""
    if _bool_param(request, "watermark", False, label=label):
        raise ValidationError(f"{label} 的 watermark 在 fal 通道不支持")
    if _bool_param(request, "return_last_frame", False, label=label):
        raise ValidationError(f"{label} 的 return_last_frame 在 fal 通道不支持")
    if _text_param(request, "draft_task_id", label):
        raise ValidationError(f"{label} 的 draft_task_id 成片流程在 fal 通道不支持")
    fmt = _text_param(request, "output_format", label).lower()
    if fmt and fmt != "mp4":
        raise ValidationError(f"{label} 的 output_format={fmt} 在 fal 通道不支持(只出 mp4)")
    if model_name == "seedance2":
        return
    if _bool_param(request, "camera_fixed", False, label=label):
        raise ValidationError(f"{label} 的 camera_fixed 在 fal 通道不支持")


def _task_25(request: GenerationRequest, *, label: str) -> str:
    """2.5:fal task = editing/extension/reference/auto。"""
    task_mode = _text_choice(request, "task_mode", _TASK_MODES_25, "auto", label=label)
    omni = _text_choice(request, "omni_reference_task_type", _OMNI_TYPES, "reference", label=label)
    if task_mode in ("edit", "extend"):
        if omni not in ("reference", task_mode):
            raise ValidationError(f"{label} 的 task_mode={task_mode} 与 omni_reference_task_type={omni} 不一致")
        return _FAL_TASK[task_mode]
    if omni in ("edit", "extend"):
        return _FAL_TASK[omni]
    return "auto"


def prepare_seedance(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """校验 + 组装 seedance2 / seedance2.5 请求体(不打网络)。"""
    label = _label(model_name)
    prompt = (request.prompt or "").strip()
    inputs = _inputs(request, label=label)
    for text in inputs.extra_text:
        if text != prompt:
            prompt = f"{prompt}\n{text}".strip() if prompt else text
    if not prompt and not for_estimate and not inputs.media:
        raise ValidationError(f"{label} 需要提示词")
    _reject_unsupported(request, model_name, label=label)

    duration = _duration_str(request, model_name, label=label)
    resolution = _resolution(request, model_name, label=label)
    ratio = _ratio(request, label=label)
    frame_mode = _text_choice(request, "frame_mode", _FRAME_MODES, "auto", label=label)
    task_mode = _text_param(request, "task_mode", label)
    is_25 = model_name == "seedance2.5"
    draft = is_25 and _bool_param(request, "draft", False, label=label)

    frames = _frame_roles(inputs, frame_mode, label=label)
    n_images = sum(1 for m in inputs.media if m.kind == MediaKind.IMAGE)
    has_av = any(m.kind != MediaKind.IMAGE for m in inputs.media)

    body: dict[str, JsonValue] = {
        "duration": duration,
        "resolution": resolution,
        "generate_audio": bool(request.generate_audio),
    }
    if prompt or for_estimate:
        body["prompt"] = prompt or "(estimate)"
    if request.seed is not None:
        body["seed"] = request.seed
    if draft:
        body["draft"] = True
        body["resolution"] = "480p"

    task = ""
    if is_25 and task_mode in ("edit", "extend"):
        task = _FAL_TASK[task_mode]
        if not has_av:
            raise ValidationError(f"{label} 的 task_mode={task_mode} 需要至少 1 段参考视频")

    if task or frame_mode == "reference" or has_av or n_images >= 3:
        mode = "reference_to_video"
    elif n_images:
        mode = "image_to_video"
    else:
        mode = "text_to_video"

    if mode == "image_to_video":
        if ratio != "auto":
            raise ValidationError(f"{label} 的图生 / 首尾帧在 fal 通道须用 adaptive 宽高比")
        if frame_mode == "first_last" and n_images > 2:
            raise ValidationError(f"{label} 的 first_last 在 fal 通道只支持 1~2 张图,多余参考图请用参考模式")
        if "first_frame" in frames:
            body["image_url"] = frames["first_frame"].uri
        if "last_frame" in frames:
            body["end_image_url"] = frames["last_frame"].uri
        if "image_url" not in body and "end_image_url" not in body:
            raise ValidationError(f"{label} 的图生模式需要至少 1 张参考图")
        body["aspect_ratio"] = "auto"
    elif mode == "reference_to_video":
        imgs, vids, auds = _ref_buckets(inputs, model_name, label=label)
        if frames:
            raise ValidationError(f"{label} 的参考模式在 fal 通道不支持首/尾帧位")
        if imgs:
            body["image_urls"] = imgs
        if vids:
            body["video_urls"] = vids
        if auds:
            body["audio_urls"] = auds
        if is_25:
            body["task"] = task or _task_25(request, label=label)
        body["aspect_ratio"] = ratio
    else:
        body["aspect_ratio"] = ratio

    return FalSubmit(
        path=f"/{SEEDANCE_FAL_ENDPOINTS[model_name][mode]}",
        app=APP_PREFIXES[model_name],
        body=body,
        prompt=prompt,
        usd=Decimal(0),
        mode=mode,
    )
