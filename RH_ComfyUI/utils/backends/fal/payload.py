"""fal 请求组装。FLUX 3 Max 文生 / 图生 / 关键帧三个端点按模式拆开。

task_mode=auto 时按输入推断:0 图→文生,1~2 图→图生(第二张作尾帧),
≥3 图或给了 frame_indices→关键帧。关键帧帧号按 24fps 计,
取值 0~时长×24;缺省时在该区间按张数均分(首帧 0、末帧 时长×24)。

MiniMax H3 Max / Turbo 的参数面不同,组包逻辑在 payload_h3.py;
``prepare`` 按模型名分流。
"""

from __future__ import annotations

import base64
from decimal import Decimal
from dataclasses import dataclass

from .billing import video_usd
from .catalog import (
    FPS,
    RATIOS,
    APP_PREFIX,
    TASK_MODES,
    RESOLUTIONS,
    ENDPOINT_IDS,
    MAX_DURATION,
    MIN_DURATION,
    MAX_KEYFRAMES,
    H3_MODEL_NAMES,
    FAL_VIDEO_MODELS,
    REASONING_LEVELS,
)
from ...core.types import MediaKind, sniff_mime_for_kind_known
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True)
class FalSubmit:
    """组装好的 fal 建单请求。"""

    path: str  # 建单路径(/owner/app/mode)
    app: str  # 状态/结果/取消共享的 owner/app 前缀
    body: dict[str, JsonValue]
    prompt: str
    usd: Decimal  # 按刊例价算出的美元成本,供积分换算;共享模型由模型侧计价,置 0
    mode: str  # text_to_video / image_to_video / keyframes_to_video / reference_to_video / edit
    # reference 模式按 1024 token/文件估算的量;settle 用上游实报 reference_tokens 替换
    ref_tokens_est: int = 0
    output: str = "video"  # video / image:结果体取 video.url 还是 images[0].url


def _label(model_name: str) -> str:
    for name, title in FAL_VIDEO_MODELS:
        if name == model_name:
            return f"{title} (fal)"
    return f"fal 模型 {model_name}"


def _text_param(request: GenerationRequest, key: str, label: str) -> str:
    """params 里的文本参数:空串等价于未提供,非字符串报错。"""
    if key not in request.params:
        return ""
    raw: object = request.params[key]
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValidationError(f"{label} 的 {key} 必须是字符串,实际收到 {type(raw).__name__}")
    return raw.strip()


def _choice(request: GenerationRequest, key: str, allowed: tuple[str, ...], default: str, *, label: str) -> str:
    val = _text_param(request, key, label)
    if not val:
        return default
    if val not in allowed:
        raise ValidationError(f"{label} 的 {key} 非法:{val!r};可选值:{', '.join(allowed)}")
    return val


def _duration(request: GenerationRequest, *, label: str) -> int:
    raw: object = request.duration
    if raw is None:
        raw = request.params["duration"] if "duration" in request.params else MIN_DURATION
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValidationError(f"{label} 的时长必须是整数秒,实际收到 {raw!r}")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw.isdigit():
            raise ValidationError(f"{label} 的时长必须是整数秒,实际收到 {raw!r}")
    val = int(raw)
    if val < MIN_DURATION or val > MAX_DURATION:
        raise ValidationError(f"{label} 时长只支持 {MIN_DURATION}~{MAX_DURATION} 秒,实际收到 {val}")
    return val


def _resolution(request: GenerationRequest, *, label: str) -> str:
    """双轨:params['resolution'] 优先,顶层 request.resolution 兜底。"""
    raw = _text_param(request, "resolution", label).lower()
    if not raw and isinstance(request.resolution, str):
        raw = request.resolution.strip().lower()
    if not raw:
        return RESOLUTIONS[0]
    if raw not in RESOLUTIONS:
        raise ValidationError(f"{label} 只支持 {'/'.join(RESOLUTIONS)}(刊例价未公布 2K/4K 档位),实际收到 {raw!r}")
    return raw


def _ratio(request: GenerationRequest, *, label: str) -> str:
    raw = _text_param(request, "ratio", label).lower()
    if not raw and isinstance(request.ratio, str):
        raw = request.ratio.strip().lower()
    if not raw:
        return "auto"
    if raw not in RATIOS:
        raise ValidationError(f"{label} 的宽高比非法:{raw!r};可选值:{', '.join(RATIOS)}")
    return raw


def _data_uri(blob: bytes, *, label: str) -> str:
    mime = sniff_mime_for_kind_known(blob, MediaKind.IMAGE) or "image/png"
    return f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}"


def _image_uris(request: GenerationRequest, *, label: str) -> list[str]:
    uris: list[str] = []
    for blob in request.images:
        if not blob:
            raise ValidationError(f"{label} 的参考图是空的")
        uris.append(_data_uri(blob, label=label))
    return uris


def _reject_ordered_media(request: GenerationRequest, *, label: str) -> None:
    if request.ordered_content:
        raise ValidationError(f"{label} 不支持多模态交错引用,请用「参考图片」上传素材")
    if request.video_refs:
        raise ValidationError(f"{label} 不支持参考视频")
    if request.audio_refs:
        raise ValidationError(f"{label} 不支持参考音频")


def _auto_frame_indices(count: int, duration: int) -> list[int]:
    """关键帧缺省位置:首帧 0、末帧 时长×24,中间均分。"""
    if count <= 1:
        return [0]
    last = duration * FPS
    return [round(i * last / (count - 1)) for i in range(count)]


def _frame_indices(request: GenerationRequest, count: int, duration: int, *, label: str) -> list[int]:
    if "frame_indices" not in request.params or not request.params["frame_indices"]:
        return _auto_frame_indices(count, duration)
    raw: object = request.params["frame_indices"]
    if not isinstance(raw, (list, tuple)):
        raise ValidationError(f"{label} 的 frame_indices 必须是整数列表")
    indices: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            raise ValidationError(f"{label} 的 frame_indices 必须是整数列表,实际收到 {item!r}")
        if isinstance(item, int):
            indices.append(item)
        elif isinstance(item, str) and item.strip().isdigit():
            indices.append(int(item.strip()))
        else:
            raise ValidationError(f"{label} 的 frame_indices 必须是整数列表,实际收到 {item!r}")
    if len(indices) != count:
        raise ValidationError(f"{label} 的 frame_indices 须与参考图一一对应(当前 {count} 张图,{len(indices)} 个帧号)")
    max_index = duration * FPS
    for idx in indices:
        if idx < 0 or idx > max_index:
            raise ValidationError(f"{label} 帧号 {idx} 超出 0~{max_index}(时长×24fps)")
    if len(set(indices)) != len(indices):
        raise ValidationError(f"{label} 的 frame_indices 帧号须唯一")
    return indices


def _mode(request: GenerationRequest, n_images: int, has_indices: bool, *, label: str) -> str:
    task_mode = _choice(request, "task_mode", TASK_MODES, "auto", label=label)
    if task_mode == "auto":
        if has_indices or n_images >= 3:
            return "keyframes_to_video"
        return "image_to_video" if n_images else "text_to_video"
    if task_mode == "text_to_video" and n_images:
        raise ValidationError(f"{label} 的 text_to_video 模式不能带参考图")
    if task_mode == "image_to_video" and not 1 <= n_images <= 2:
        raise ValidationError(f"{label} 的 image_to_video 模式需要 1~2 张参考图(首帧 / 首尾帧)")
    if task_mode == "keyframes_to_video" and n_images < 1:
        raise ValidationError(f"{label} 的 keyframes_to_video 模式需要至少 1 张关键帧图")
    return task_mode


def prepare(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """校验 + 组装 fal 请求体(不打网络),按模型分流到具体实现。"""
    if model_name in H3_MODEL_NAMES:
        from .payload_h3 import prepare_h3

        return prepare_h3(model_name, request, for_estimate=for_estimate)
    if model_name == "minimax_h3":
        from .payload_minimax_h3 import prepare_minimax_h3

        return prepare_minimax_h3(model_name, request, for_estimate=for_estimate)
    if model_name == "minimax_h3_image":
        from .payload_minimax_h3_image import prepare_minimax_h3_image

        return prepare_minimax_h3_image(model_name, request, for_estimate=for_estimate)
    if model_name in ("seedance2", "seedance2.5"):
        from .payload_seedance import prepare_seedance

        return prepare_seedance(model_name, request, for_estimate=for_estimate)
    if model_name == "gpt-image-2" or model_name.startswith("banana"):
        from .payload_image import prepare_image

        return prepare_image(model_name, request, for_estimate=for_estimate)
    return _prepare_flux3max(model_name, request, for_estimate=for_estimate)


def _prepare_flux3max(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """校验 + 组装 fal 请求体(不打网络)。

    for_estimate=True 时允许空 prompt 等缺省,仅用于积分估算,
    不发起真实上游调用。
    """
    label = _label(model_name)
    prompt = (request.prompt or "").strip()
    if not prompt and not for_estimate:
        raise ValidationError(f"{label} 需要提示词")
    _reject_ordered_media(request, label=label)
    if len(request.images) > MAX_KEYFRAMES:
        raise ValidationError(f"{label} 关键帧最多 {MAX_KEYFRAMES} 张,实际收到 {len(request.images)}")

    duration = _duration(request, label=label)
    resolution = _resolution(request, label=label)
    ratio = _ratio(request, label=label)
    reasoning = _choice(request, "reasoning_level", REASONING_LEVELS, "high", label=label)
    uris = _image_uris(request, label=label)
    has_indices = "frame_indices" in request.params and bool(request.params["frame_indices"])
    mode = _mode(request, len(uris), has_indices, label=label)

    body: dict[str, JsonValue] = {
        "prompt": prompt or "(estimate)",
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": ratio,
        "reasoning_level": reasoning,
        "generate_audio": bool(request.generate_audio),
    }
    if request.seed is not None:
        body["seed"] = request.seed

    if mode == "image_to_video":
        body["image_url"] = uris[0]
        if len(uris) > 1:
            body["end_image_url"] = uris[1]
    elif mode == "keyframes_to_video":
        indices = _frame_indices(request, len(uris), duration, label=label)
        pairs = sorted(zip(indices, uris), key=lambda p: p[0])
        body["keyframes"] = [{"frame_index": idx, "image_url": uri} for idx, uri in pairs]

    return FalSubmit(
        path=f"/{ENDPOINT_IDS[mode]}",
        app=APP_PREFIX,
        body=body,
        prompt=prompt,
        usd=video_usd(resolution, duration),
        mode=mode,
    )
