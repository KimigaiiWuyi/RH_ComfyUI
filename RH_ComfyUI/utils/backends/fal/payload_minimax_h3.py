"""fal minimax/h3(普通 MiniMax H3)请求组装。

插件已有模型 minimax_h3(task_mode: auto/t2v/i2v/first_last/reference,
frame_mode: auto/first_frame/last_frame/first_last/reference)挂到 fal 通道;
与 h3max/h3maxturbo 的区别:无关键帧位、分辨率为 480P/768P/2K/4K、时长整数 5~15。
fal 表达不了的请求(watermark / 参考模式带帧位)抛 ValidationError,由通道转成
CAPABILITY 换源。
"""

from __future__ import annotations

from decimal import Decimal

from .catalog import (
    MMH3_RATIOS,
    APP_PREFIXES,
    MMH3_ENDPOINTS,
    MMH3_MAX_DURATION,
    MMH3_MIN_DURATION,
    MMH3_EXPANSION_MODES,
    MMH3_MAX_REFERENCE_FILES,
    MMH3_MAX_REFERENCE_AUDIOS,
    MMH3_MAX_REFERENCE_IMAGES,
    MMH3_MAX_REFERENCE_VIDEOS,
)
from .payload import FalSubmit, JsonValue, _label, _choice, _text_param
from .payload_h3 import _Media, _Inputs, _inputs, _bool_param, _number_param
from ...core.types import MediaKind
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

_MM_TASK_MODES = ("auto", "t2v", "i2v", "first_last", "reference")
_MM_FRAME_MODES = ("auto", "first_frame", "last_frame", "first_last", "reference")
_FRAME_ROLES = {"first_frame", "last_frame", "middle_frame"}


def _duration(request: GenerationRequest, *, label: str) -> int:
    """双轨时长:顶层字段优先,params['duration'] 兜底;fal 侧整数 5~15。"""
    raw: object = request.duration
    if raw is None:
        raw = request.params["duration"] if "duration" in request.params else 5
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的时长必须是整数秒,实际收到 {raw!r}")
    if isinstance(raw, float):
        if raw != int(raw):
            raise ValidationError(f"{label} 的时长必须是整数秒,实际收到 {raw!r}")
        raw = int(raw)
    elif isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int):
        raise ValidationError(f"{label} 的时长必须是整数秒,实际收到 {raw!r}")
    if raw < MMH3_MIN_DURATION or raw > MMH3_MAX_DURATION:
        raise ValidationError(f"{label} 的时长 {raw}s 超出 fal 通道支持范围 {MMH3_MIN_DURATION}~{MMH3_MAX_DURATION}s")
    return raw


def _resolution(request: GenerationRequest, *, label: str) -> str:
    """插件档位 768p/2k → fal 的 768P/2K(fal 另有 480P/4K,插件不暴露)。"""
    raw = _text_param(request, "resolution", label).lower()
    if not raw and isinstance(request.resolution, str):
        raw = request.resolution.strip().lower()
    if not raw or raw == "2k":
        return "2K"
    if raw == "768p":
        return "768P"
    raise ValidationError(f"{label} 在 fal 通道只支持 768P/2K,实际收到 {raw!r}")


def _ratio(request: GenerationRequest, *, label: str) -> str:
    raw = _text_param(request, "ratio", label).lower()
    if not raw and isinstance(request.ratio, str):
        raw = request.ratio.strip().lower()
    if not raw:
        return "16:9"
    if raw == "adaptive":
        return "adaptive"
    if raw not in MMH3_RATIOS:
        raise ValidationError(f"{label} 的宽高比非法:{raw!r};可选值:{', '.join(MMH3_RATIOS)}")
    return raw


def _frame_roles(inputs: _Inputs, frame_mode: str, *, label: str) -> dict[str, _Media]:
    """OC 按 role、flat 按 frame_mode 分配 首/尾帧;非图片帧位报错。"""
    for m in inputs.media:
        if m.role in _FRAME_ROLES and m.kind != MediaKind.IMAGE:
            raise ValidationError(f"{label} 的 {m.role} 帧位只能是图片,实际收到 {m.kind.value}")
    imgs = [m for m in inputs.media if m.kind == MediaKind.IMAGE]
    frames: dict[str, _Media] = {}
    if inputs.oc_order:
        for m in imgs:
            if m.role in ("first_frame", "last_frame"):
                if m.role in frames:
                    raise ValidationError(f"{label} 的 {m.role} 只能有一张")
                frames[m.role] = m
        return frames
    if frame_mode == "first_frame":
        if imgs:
            frames["first_frame"] = imgs[0]
    elif frame_mode == "last_frame":
        if imgs:
            frames["last_frame"] = imgs[-1]
    elif frame_mode == "first_last":
        if imgs:
            frames["first_frame"] = imgs[0]
        if len(imgs) > 1:
            frames["last_frame"] = imgs[1]
    return frames


def _mode(request: GenerationRequest, inputs: _Inputs, *, label: str) -> str:
    """插件 task_mode/frame_mode → fal 端点模式(text/image/reference)。"""
    task_mode = _choice(request, "task_mode", _MM_TASK_MODES, "auto", label=label)
    frame_mode = _choice(request, "frame_mode", _MM_FRAME_MODES, "auto", label=label)
    media = inputs.media
    n_images = sum(1 for m in media if m.kind == MediaKind.IMAGE)
    has_av = any(m.kind != MediaKind.IMAGE for m in media)

    if frame_mode == "reference":
        if not media:
            raise ValidationError(f"{label} 的 frame_mode=reference 需要至少 1 个参考素材")
        return "reference_to_video"
    if task_mode == "t2v":
        if media:
            raise ValidationError(f"{label} 的 t2v 模式不能带参考素材")
        return "text_to_video"
    if task_mode == "i2v":
        if has_av or n_images != 1:
            raise ValidationError(f"{label} 的 i2v 模式需要恰好 1 张参考图,且不带音视频")
        return "image_to_video"
    if task_mode == "first_last":
        if has_av:
            raise ValidationError(f"{label} 的 first_last 模式不能带音视频参考")
        if not n_images:
            raise ValidationError(f"{label} 的 first_last 模式需要至少 1 张参考图")
        return "image_to_video"
    if task_mode == "reference":
        if not media:
            raise ValidationError(f"{label} 的 reference 模式需要至少 1 个参考素材")
        return "reference_to_video"
    # auto:0 素材→文生;≤2 图且无音视频且无 OC → 图生;其余→参考
    if has_av or n_images >= 3 or inputs.oc_order and media:
        return "reference_to_video"
    return "image_to_video" if n_images else "text_to_video"


def prepare_minimax_h3(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """校验 + 组装 minimax/h3 请求体(不打网络)。"""
    label = _label(model_name)
    prompt = (request.prompt or "").strip()
    inputs = _inputs(request, label=label)
    for text in inputs.extra_text:
        if text != prompt:
            prompt = f"{prompt}\n{text}".strip() if prompt else text
    if not prompt and not for_estimate:
        raise ValidationError(f"{label} 需要提示词")
    if _bool_param(request, "watermark", False, label=label):
        raise ValidationError(f"{label} 的 watermark 在 fal 通道不支持")
    if request.generate_audio is False:
        raise ValidationError(f"{label} 输出恒含原生音频,fal 通道不支持 generate_audio=False")
    if request.seed is not None:
        raise ValidationError(f"{label} 的 seed 在 fal 通道不支持")
    if _number_param(request, "middle_frame_time", label=label) is not None:
        raise ValidationError(f"{label} 不支持中间帧位")

    duration = _duration(request, label=label)
    resolution = _resolution(request, label=label)
    ratio = _ratio(request, label=label)
    expansion = _choice(request, "prompt_expansion", MMH3_EXPANSION_MODES, "", label=label)
    frame_mode = _choice(request, "frame_mode", _MM_FRAME_MODES, "auto", label=label)
    frames = _frame_roles(inputs, frame_mode, label=label)
    mode = _mode(request, inputs, label=label)

    if ratio == "adaptive" and mode != "reference_to_video":
        raise ValidationError(f"{label} 的 adaptive 宽高比仅 reference 模式可用")

    body: dict[str, JsonValue] = {
        "prompt": prompt or "(estimate)",
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": ratio,
    }
    if expansion:
        body["prompt_expansion_mode"] = expansion

    if mode == "image_to_video":
        if "first_frame" in frames:
            body["image_url"] = frames["first_frame"].uri
        if "last_frame" in frames:
            body["end_image_url"] = frames["last_frame"].uri
        if not frames:
            imgs = [m for m in inputs.media if m.kind == MediaKind.IMAGE]
            body["image_url"] = imgs[0].uri
            if len(imgs) > 1:
                body["end_image_url"] = imgs[1].uri
    elif mode == "reference_to_video":
        if frames:
            raise ValidationError(f"{label} 的 reference 模式在 fal 通道不支持首/尾帧位")
        ref_imgs: list[JsonValue] = [m.uri for m in inputs.media if m.kind == MediaKind.IMAGE]
        ref_vids: list[JsonValue] = [m.uri for m in inputs.media if m.kind == MediaKind.VIDEO]
        ref_auds: list[JsonValue] = [m.uri for m in inputs.media if m.kind == MediaKind.AUDIO]
        if len(ref_imgs) > MMH3_MAX_REFERENCE_IMAGES:
            raise ValidationError(f"{label} 参考图最多 {MMH3_MAX_REFERENCE_IMAGES} 张,实际收到 {len(ref_imgs)}")
        if len(ref_vids) > MMH3_MAX_REFERENCE_VIDEOS:
            raise ValidationError(f"{label} 参考视频最多 {MMH3_MAX_REFERENCE_VIDEOS} 段,实际收到 {len(ref_vids)}")
        if len(ref_auds) > MMH3_MAX_REFERENCE_AUDIOS:
            raise ValidationError(f"{label} 参考音频最多 {MMH3_MAX_REFERENCE_AUDIOS} 段,实际收到 {len(ref_auds)}")
        total = len(ref_imgs) + len(ref_vids) + len(ref_auds)
        if total > MMH3_MAX_REFERENCE_FILES:
            raise ValidationError(f"{label} 参考素材共 {total} 个,超过上限 {MMH3_MAX_REFERENCE_FILES} 个")
        if ref_imgs:
            body["reference_image_urls"] = ref_imgs
        if ref_vids:
            body["reference_video_urls"] = ref_vids
        if ref_auds:
            body["reference_audio_urls"] = ref_auds
    else:
        if frame_mode not in ("auto", "reference"):
            raise ValidationError(f"{label} 的文生模式不支持 frame_mode={frame_mode}")

    return FalSubmit(
        path=f"/{MMH3_ENDPOINTS[mode]}",
        app=APP_PREFIXES["minimax_h3"],
        body=body,
        prompt=prompt,
        usd=Decimal(0),
        mode=mode,
    )
