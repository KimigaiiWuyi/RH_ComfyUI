"""fal MiniMax H3 Max / H3 Max Turbo 请求组装。

task_mode=auto 分流:有 ordered_content 媒体 / 参考视频 / 参考音频 / ≥3 图
→reference_to_video;1~2 图→image_to_video;0 图→text_to_video。
reference 模式:扁平字段按类型分桶,ordered_content 保序走
reference_conditions;关键帧位(首/中/尾帧)仅 h3max 支持,由
frame_mode(flat)或媒体 role=first_frame/last_frame/middle_frame(OC)指定。
"""

from __future__ import annotations

import base64
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass

from .billing import h3_video_usd, h3_ref_tokens_usd
from .catalog import (
    H3_RATIOS,
    APP_PREFIXES,
    H3_TASK_MODES,
    H3_FRAME_MODES,
    H3_RESOLUTIONS,
    H3_ENDPOINT_IDS,
    H3_MAX_DURATION,
    H3_MIN_DURATION,
    H3_EXPANSION_MODES,
    H3_DEFAULT_DURATION,
    H3_MAX_REFERENCE_FILES,
    H3_MAX_REFERENCE_AUDIOS,
    H3_MAX_REFERENCE_IMAGES,
    H3_MAX_REFERENCE_VIDEOS,
)
from .payload import FalSubmit, JsonValue, _label, _choice, _text_param
from ...core.types import MediaRef, MediaKind, ContentItemType, sniff_mime_for_kind_known
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

# 关键帧位 role 名(OC 媒体项)
_ROLE_FIRST = "first_frame"
_ROLE_LAST = "last_frame"
_ROLE_MIDDLE = "middle_frame"
_FRAME_ROLES = {_ROLE_FIRST, _ROLE_LAST, _ROLE_MIDDLE}


@dataclass(frozen=True)
class _Media:
    """收集好的单条参考素材。"""

    kind: MediaKind
    uri: str
    role: str  # ""/reference 或关键帧位


@dataclass(frozen=True)
class _Inputs:
    """归一化后的媒体与文本输入。"""

    media: list[_Media]
    oc_order: bool  # True=媒体来自 ordered_content,顺序语义保留
    extra_text: list[str]  # OC 里的文本段,并入 prompt


def _ref_uri(ref: MediaRef, *, label: str) -> str:
    """MediaRef → 上游可取的 URL:url 透传,data 转 data URI。"""
    if isinstance(ref.url, str) and ref.url.strip():
        return ref.url.strip()
    data = ref.data
    if not data:
        raise ValidationError(f"{label} 的参考素材缺少内容(data/url 至少其一)")
    mime = ref.mime_type or sniff_mime_for_kind_known(data, ref.kind) or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _flat_inputs(request: GenerationRequest, *, label: str) -> _Inputs:
    media: list[_Media] = []
    for blob in request.images:
        if not blob:
            raise ValidationError(f"{label} 的参考图是空的")
        mime = sniff_mime_for_kind_known(blob, MediaKind.IMAGE) or "image/png"
        media.append(_Media(MediaKind.IMAGE, f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}", ""))
    for ref in request.video_refs:
        media.append(_Media(MediaKind.VIDEO, _ref_uri(ref, label=label), ref.role or ""))
    for ref in request.audio_refs:
        media.append(_Media(MediaKind.AUDIO, _ref_uri(ref, label=label), ref.role or ""))
    return _Inputs(media=media, oc_order=False, extra_text=[])


def _ordered_inputs(request: GenerationRequest, *, label: str) -> _Inputs:
    """ordered_content → 有序素材 + 文本段;text 段并入 prompt。"""
    media: list[_Media] = []
    texts: list[str] = []
    for item in request.ordered_content:
        if item.type == ContentItemType.TEXT:
            if item.text and item.text.strip():
                texts.append(item.text.strip())
            continue
        if item.type == ContentItemType.DRAFT_TASK:
            raise ValidationError(f"{label} 不支持样片(draft_task)引用")
        if item.media is None:
            continue
        role = (item.media.role or item.role or "").strip().lower()
        media.append(_Media(item.media.kind, _ref_uri(item.media, label=label), role))
    return _Inputs(media=media, oc_order=True, extra_text=texts)


def _inputs(request: GenerationRequest, *, label: str) -> _Inputs:
    if request.ordered_content:
        return _ordered_inputs(request, label=label)
    return _flat_inputs(request, label=label)


def _duration(request: GenerationRequest, *, label: str) -> Decimal:
    """双轨读时长:顶层字段优先,params['duration'] 兜底。浮点秒 0.92~15。"""
    raw: object = request.duration
    if raw is None:
        raw = request.params["duration"] if "duration" in request.params else H3_DEFAULT_DURATION
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的时长必须是数值秒,实际收到 {raw!r}")
    if isinstance(raw, (int, float)):
        val = Decimal(str(raw))
    elif isinstance(raw, str) and raw.strip():
        try:
            val = Decimal(raw.strip())
        except InvalidOperation:
            raise ValidationError(f"{label} 的时长必须是数值秒,实际收到 {raw!r}") from None
    else:
        raise ValidationError(f"{label} 的时长必须是数值秒,实际收到 {raw!r}")
    if val < Decimal(str(H3_MIN_DURATION)) or val > Decimal(str(H3_MAX_DURATION)):
        raise ValidationError(f"{label} 时长只支持 {H3_MIN_DURATION}~{H3_MAX_DURATION} 秒,实际收到 {val}")
    return val


def _resolution(request: GenerationRequest, *, label: str) -> str:
    raw = _text_param(request, "resolution", label).lower()
    if not raw and isinstance(request.resolution, str):
        raw = request.resolution.strip().lower()
    if not raw:
        return "768p"
    if raw not in H3_RESOLUTIONS:
        raise ValidationError(f"{label} 只支持 {'/'.join(H3_RESOLUTIONS)},实际收到 {raw!r}")
    return raw


def _ratio(request: GenerationRequest, *, label: str) -> str:
    raw = _text_param(request, "ratio", label).lower()
    if not raw and isinstance(request.ratio, str):
        raw = request.ratio.strip().lower()
    if not raw:
        return "auto"
    if raw not in H3_RATIOS:
        raise ValidationError(f"{label} 的宽高比非法:{raw!r};可选值:{', '.join(H3_RATIOS)}")
    return raw


def _bool_param(request: GenerationRequest, key: str, default: bool, *, label: str) -> bool:
    if key not in request.params or request.params[key] is None:
        return default
    raw: object = request.params[key]
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    raise ValidationError(f"{label} 的 {key} 必须是布尔值,实际收到 {raw!r}")


def _number_param(request: GenerationRequest, key: str, *, label: str) -> Decimal | None:
    if key not in request.params or request.params[key] is None:
        return None
    raw: object = request.params[key]
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的 {key} 必须是数值,实际收到 {raw!r}")
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    if isinstance(raw, str) and raw.strip():
        try:
            return Decimal(raw.strip())
        except InvalidOperation:
            pass
    raise ValidationError(f"{label} 的 {key} 必须是数值,实际收到 {raw!r}")


def _mode(request: GenerationRequest, inputs: _Inputs, *, label: str) -> str:
    task_mode = _choice(request, "task_mode", H3_TASK_MODES, "auto", label=label)
    media = inputs.media
    n_images = sum(1 for m in media if m.kind == MediaKind.IMAGE)
    has_av = any(m.kind != MediaKind.IMAGE for m in media)
    has_oc = inputs.oc_order and bool(media)
    if task_mode == "auto":
        if has_oc or has_av or n_images >= 3:
            return "reference_to_video"
        return "image_to_video" if n_images else "text_to_video"
    if task_mode == "text_to_video" and media:
        raise ValidationError(f"{label} 的 text_to_video 模式不能带参考素材")
    if task_mode == "image_to_video":
        if has_oc or has_av:
            raise ValidationError(f"{label} 的 image_to_video 模式只接受 1~2 张参考图")
        if not 1 <= n_images <= 2:
            raise ValidationError(f"{label} 的 image_to_video 模式需要 1~2 张参考图(首帧 / 首尾帧)")
    if task_mode == "reference_to_video" and not media:
        raise ValidationError(f"{label} 的 reference_to_video 模式需要至少 1 个参考素材")
    return task_mode


def _frames(media: list[_Media], frame_mode: str, *, oc_order: bool, turbo: bool, label: str) -> dict[str, _Media]:
    """关键帧位分配:first/middle/last → _Media。

    OC 路径按媒体 role 指定;flat 路径按 frame_mode 取 images 前 N 张。
    """
    frames: dict[str, _Media] = {}
    if oc_order:
        for m in media:
            if m.role in _FRAME_ROLES:
                if m.kind != MediaKind.IMAGE:
                    raise ValidationError(f"{label} 的 {m.role} 帧位只能是图片,实际收到 {m.kind.value}")
                if m.role in frames:
                    raise ValidationError(f"{label} 的 {m.role} 关键帧只能有一张")
                frames[m.role] = m
        return frames

    imgs = [m for m in media if m.kind == MediaKind.IMAGE]
    for m in media:
        if m.kind != MediaKind.IMAGE and m.role in _FRAME_ROLES:
            raise ValidationError(f"{label} 的 {m.role} 帧位只能是图片,实际收到 {m.kind.value}")
    if frame_mode in ("auto", "reference"):
        return frames
    if not imgs:
        raise ValidationError(f"{label} 的 frame_mode={frame_mode} 需要至少 1 张参考图")
    if frame_mode == "last_only":
        frames[_ROLE_LAST] = imgs[0]
        return frames
    if frame_mode == "first_last":
        frames[_ROLE_FIRST] = imgs[0]
        if len(imgs) > 1:
            frames[_ROLE_LAST] = imgs[1]
        return frames
    # first_middle_last:前 3 张依次 首/中/尾
    if len(imgs) < 3:
        raise ValidationError(f"{label} 的 first_middle_last 需要至少 3 张参考图(首/中/尾帧)")
    frames[_ROLE_FIRST] = imgs[0]
    frames[_ROLE_MIDDLE] = imgs[1]
    frames[_ROLE_LAST] = imgs[2]
    return frames


def _ref_media(
    media: list[_Media],
    frames: dict[str, _Media],
    frame_mode: str,
    *,
    oc_order: bool,
) -> tuple[list[JsonValue], list[JsonValue], list[JsonValue]]:
    """剩余素材按类型分桶;OC 关键帧项不重复进参考列表。"""
    keyed = {id(m) for m in frames.values()}
    imgs: list[JsonValue] = []
    vids: list[JsonValue] = []
    auds: list[JsonValue] = []
    for m in media:
        if id(m) in keyed:
            continue
        if m.kind == MediaKind.IMAGE:
            imgs.append(m.uri)
        elif m.kind == MediaKind.VIDEO:
            vids.append(m.uri)
        else:
            auds.append(m.uri)
    return imgs, vids, auds


def _conditions(media: list[_Media], frames: dict[str, _Media]) -> list[JsonValue]:
    """OC 保序 → reference_conditions;关键帧项独立成字段不入列。"""
    keyed = {id(m) for m in frames.values()}
    out: list[JsonValue] = []
    for m in media:
        if id(m) in keyed:
            continue
        out.append({"type": m.kind.value, "url": m.uri})
    return out


def prepare_h3(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """校验 + 组装 H3 请求体(不打网络)。"""
    turbo = model_name == "h3maxturbo"
    label = _label(model_name)
    prompt = (request.prompt or "").strip()
    inputs = _inputs(request, label=label)
    # OC 文本段并入 prompt(调用方常把提示词放在首个 text 段,避免重复)
    for text in inputs.extra_text:
        if text != prompt:
            prompt = f"{prompt}\n{text}".strip() if prompt else text
    if not prompt and not for_estimate:
        raise ValidationError(f"{label} 需要提示词")
    if request.generate_audio is False:
        raise ValidationError(f"{label} 输出恒含原生音频,不支持 generate_audio=False")

    duration = _duration(request, label=label)
    resolution = _resolution(request, label=label)
    ratio = _ratio(request, label=label)
    expansion = _choice(request, "prompt_expansion", H3_EXPANSION_MODES, "balanced", label=label)
    safety = _bool_param(request, "safety_checker", True, label=label)
    target_audio = _text_param(request, "target_audio_url", label)
    frame_mode = _choice(request, "frame_mode", H3_FRAME_MODES, "auto", label=label)
    middle_time = _number_param(request, "middle_frame_time", label=label)

    mode = _mode(request, inputs, label=label)
    media = inputs.media

    if ratio == "adaptive" and mode != "reference_to_video":
        raise ValidationError(f"{label} 的 adaptive 宽高比仅 reference_to_video 模式可用")

    body: dict[str, JsonValue] = {
        "prompt": prompt or "(estimate)",
        "duration": int(duration) if duration == duration.to_integral_value() else float(duration),
        "resolution": resolution.upper(),
        "prompt_expansion_mode": expansion,
        "enable_safety_checker": safety,
    }
    if ratio != "auto":
        body["aspect_ratio"] = ratio
    if request.seed is not None:
        body["seed"] = request.seed

    ref_tokens_est = 0
    if mode == "image_to_video":
        if frame_mode in ("reference", "first_middle_last"):
            raise ValidationError(f"{label} 的 image_to_video 不支持 frame_mode={frame_mode}")
        if middle_time is not None:
            raise ValidationError(f"{label} 的 middle_frame_time 只在参考模式生效")
        imgs = [m for m in media if m.kind == MediaKind.IMAGE]
        if frame_mode == "last_only":
            body["end_image_url"] = imgs[0].uri
            if len(imgs) > 1:
                raise ValidationError(f"{label} 的 last_only 只取第 1 张参考图作尾帧")
        else:
            body["image_url"] = imgs[0].uri
            if len(imgs) > 1:
                body["end_image_url"] = imgs[1].uri

    elif mode == "reference_to_video":
        frames = _frames(media, frame_mode, oc_order=inputs.oc_order, turbo=turbo, label=label)
        if frames and turbo:
            raise ValidationError(f"{label} 参考模式不支持关键帧位(h3maxturbo 上游无此参数)")
        imgs, vids, auds = _ref_media(media, frames, frame_mode, oc_order=inputs.oc_order)
        if len(imgs) > H3_MAX_REFERENCE_IMAGES:
            raise ValidationError(f"{label} 参考图最多 {H3_MAX_REFERENCE_IMAGES} 张,实际收到 {len(imgs)}")
        if len(vids) > H3_MAX_REFERENCE_VIDEOS:
            raise ValidationError(f"{label} 参考视频最多 {H3_MAX_REFERENCE_VIDEOS} 段,实际收到 {len(vids)}")
        if len(auds) > H3_MAX_REFERENCE_AUDIOS:
            raise ValidationError(f"{label} 参考音频最多 {H3_MAX_REFERENCE_AUDIOS} 段,实际收到 {len(auds)}")
        if len(imgs) + len(vids) + len(auds) > H3_MAX_REFERENCE_FILES:
            raise ValidationError(
                f"{label} 参考素材共 {len(imgs) + len(vids) + len(auds)} 个,超过上限 {H3_MAX_REFERENCE_FILES} 个"
            )
        if inputs.oc_order:
            body["reference_conditions"] = _conditions(media, frames)
        else:
            if imgs:
                body["reference_image_urls"] = imgs
            if vids:
                body["reference_video_urls"] = vids
            if auds:
                body["reference_audio_urls"] = auds
        if _ROLE_FIRST in frames:
            body["image_url"] = frames[_ROLE_FIRST].uri
        if _ROLE_MIDDLE in frames:
            if _ROLE_FIRST not in frames or _ROLE_LAST not in frames:
                raise ValidationError(f"{label} 中间帧需要同时给出首帧与尾帧")
            if resolution == "1080p":
                raise ValidationError(f"{label} 中间帧只支持原生 480p/768p,不支持 1080p 精修")
            body["middle_image_url"] = frames[_ROLE_MIDDLE].uri
            mft = middle_time if middle_time is not None else duration / Decimal("2")
            if mft <= 0 or mft >= duration:
                raise ValidationError(f"{label} 的 middle_frame_time 必须落在 0~{duration} 秒内")
            body["middle_frame_time"] = float(mft)
        elif middle_time is not None:
            raise ValidationError(f"{label} 的 middle_frame_time 需要配合中间帧图")
        if _ROLE_LAST in frames:
            body["end_image_url"] = frames[_ROLE_LAST].uri
        # 官方:参考素材 + 关键帧位都计入 reference token;每文件按 1024 token 估算
        ref_tokens_est = (len(imgs) + len(vids) + len(auds) + len(frames)) * 1024
    else:
        # text_to_video:frame_mode / middle_frame_time 无意义
        if frame_mode != "auto":
            raise ValidationError(f"{label} 的 text_to_video 不支持 frame_mode={frame_mode}")
        if middle_time is not None:
            raise ValidationError(f"{label} 的 middle_frame_time 只在参考模式生效")

    if target_audio:
        if duration < Decimal("2"):
            raise ValidationError(f"{label} 的 target_audio_url 要求时长 ≥2 秒,实际收到 {duration}")
        if mode == "reference_to_video" and any(m.kind == MediaKind.AUDIO for m in media):
            raise ValidationError(f"{label} 的 target_audio_url 不能与参考音频同用")
        body["target_audio_url"] = target_audio

    usd = h3_video_usd(model_name, mode, resolution, duration) + h3_ref_tokens_usd(model_name, ref_tokens_est)
    return FalSubmit(
        path=f"/{H3_ENDPOINT_IDS[model_name][mode]}",
        app=APP_PREFIXES[model_name],
        body=body,
        prompt=prompt,
        usd=usd,
        mode=mode,
        ref_tokens_est=ref_tokens_est,
    )
