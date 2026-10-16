"""可灵请求组装。按模型能力选路径,校验零网络。

视频新接口:文生用顶层 prompt,图生 / Omni 用 contents[]。
图片仍走 /v1,body 里带 model_name。
``/image-to-video/kling-3.0`` 的字段按同系列图生接口推断,官方该页本次没有取到。
"""

from __future__ import annotations

import base64
from decimal import Decimal
from dataclasses import dataclass

from .billing import image_yuan, video_yuan
from .catalog import (
    POLL_TASKS,
    IMAGE_RATIOS,
    POLL_IMAGE_OMNI,
    POLL_IMAGE_MULTI,
    POLL_IMAGE_EXPAND,
    KLING_IMAGE_MODELS,
    KLING_VIDEO_MODELS,
    POLL_IMAGE_SUBJECT,
    IMAGE_RATIOS_WITH_AUTO,
    POLL_IMAGE_GENERATIONS,
)
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_VIDEO_NAMES = {name for name, _title in KLING_VIDEO_MODELS}
_IMAGE_NAMES = {name for name, _title in KLING_IMAGE_MODELS}
_TITLES = dict(KLING_VIDEO_MODELS + KLING_IMAGE_MODELS)
_VIDEO_RATIO_SET = {"16:9", "9:16", "1:1"}
_IMAGE_RATIO_SET = set(IMAGE_RATIOS)
_IMAGE_RATIO_AUTO = set(IMAGE_RATIOS_WITH_AUTO)


@dataclass(frozen=True)
class KlingSubmit:
    """一次上游创建请求。``yuan`` 是这一单的人民币,积分由调用方换算。"""

    path: str
    poll_token: str
    body: dict[str, JsonValue]
    kind: str
    prompt: str
    yuan: Decimal


def prepare(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> KlingSubmit:
    """组装并校验。``for_estimate`` 时允许空提示词,能力冲突仍然拒绝。"""
    if model_name in _VIDEO_NAMES:
        return _prepare_video(model_name, request, for_estimate=for_estimate)
    if model_name in _IMAGE_NAMES:
        return _prepare_image(model_name, request, for_estimate=for_estimate)
    raise ValidationError(f"未知可灵模型 {model_name}")


def _title(model_name: str) -> str:
    return _TITLES[model_name] if model_name in _TITLES else model_name


def _reject_ordered_media(request: GenerationRequest, label: str) -> None:
    for item in request.ordered_content:
        if item.media is not None:
            raise ValidationError(f"{label} 请把图片放在 images,视频放在 video_refs")


def _b64(blob: bytes, label: str) -> str:
    if not blob:
        raise ValidationError(f"{label} 的参考图是空的")
    return base64.b64encode(blob).decode("ascii")


def _images(request: GenerationRequest, *, maximum: int, label: str) -> list[str]:
    if len(request.images) > maximum:
        raise ValidationError(f"{label} 最多 {maximum} 张参考图,当前 {len(request.images)} 张")
    return [_b64(blob, label) for blob in request.images]


def _video_urls(request: GenerationRequest, label: str) -> list[str]:
    if len(request.video_refs) > 1:
        raise ValidationError(f"{label} 最多 1 段参考视频")
    urls: list[str] = []
    for ref in request.video_refs:
        url = ref.url.strip() if isinstance(ref.url, str) else ""
        if not url:
            raise ValidationError(f"{label} 的参考视频只接受可访问的 URL")
        urls.append(url)
    return urls


def _prompt(request: GenerationRequest, *, limit: int, required: bool, label: str) -> str:
    text = request.prompt or ""
    if required and not text.strip():
        raise ValidationError(f"{label} 必须填写提示词")
    if len(text) > limit:
        raise ValidationError(f"{label} 提示词最长 {limit} 个字符,当前 {len(text)} 个")
    return text


def _duration(request: GenerationRequest, allowed: set[int], label: str) -> int:
    value = int(request.duration)
    if value not in allowed:
        shown = ", ".join(str(item) for item in sorted(allowed))
        raise ValidationError(f"{label} 时长不支持 {value} 秒,可选: {shown}")
    return value


def _resolution(request: GenerationRequest, allowed: set[str], *, default: str, label: str) -> str:
    raw = (request.resolution or "").strip().lower()
    if not raw:
        raw = default
    if raw not in allowed:
        shown = ", ".join(sorted(allowed))
        raise ValidationError(f"{label} 不支持分辨率 {raw},可选: {shown}")
    return raw


def _ratio(request: GenerationRequest, allowed: set[str], *, default: str, label: str) -> str:
    raw = (request.ratio or "").strip()
    if not raw:
        return default
    if raw not in allowed:
        shown = ", ".join(sorted(allowed))
        raise ValidationError(f"{label} 不支持宽高比 {raw},可选: {shown}")
    return raw


def _bool_param(request: GenerationRequest, key: str, default: bool, label: str) -> bool:
    if key not in request.params:
        return default
    raw = request.params[key]
    if isinstance(raw, bool):
        return raw
    raise ValidationError(f"{label} 的 {key} 必须是布尔值")


def _int_param(request: GenerationRequest, key: str, default: int, low: int, high: int, label: str) -> int:
    if key not in request.params:
        return default
    raw = request.params[key]
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的 {key} 必须是整数")
    value: int
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        value = int(raw.strip())
    else:
        raise ValidationError(f"{label} 的 {key} 必须是整数")
    if value < low or value > high:
        raise ValidationError(f"{label} 的 {key} 须在 {low}~{high},当前 {value}")
    return value


def _text_param(request: GenerationRequest, key: str, label: str) -> str:
    if key not in request.params:
        return ""
    raw = request.params[key]
    if isinstance(raw, str):
        return raw.strip()
    raise ValidationError(f"{label} 的 {key} 必须是字符串")


def _choice(request: GenerationRequest, key: str, allowed: set[str], default: str, label: str) -> str:
    if key not in request.params:
        return default
    raw = request.params[key]
    if isinstance(raw, str) and raw in allowed:
        return raw
    shown = ", ".join(sorted(allowed))
    raise ValidationError(f"{label} 的 {key} 不支持 {raw!s},可选: {shown}")


def _element_ids(request: GenerationRequest, label: str) -> list[str]:
    if "element_ids" in request.params:
        raw = request.params["element_ids"]
        if not isinstance(raw, list):
            raise ValidationError(f"{label} 的 element_ids 必须是字符串列表")
        found: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise ValidationError(f"{label} 的 element_ids 含有空项")
            found.append(item.strip())
        return found
    single = _text_param(request, "element_id", label)
    return [single] if single else []


def _element_value(text: str) -> JsonValue:
    if text.isdigit():
        return int(text)
    return text


def _voice_ids(request: GenerationRequest, label: str) -> list[str]:
    found: list[str] = []
    if isinstance(request.voice_id, str) and request.voice_id.strip():
        found.append(request.voice_id.strip())
    extra = _text_param(request, "voice_id", label)
    if extra and extra not in found:
        found.append(extra)
    if "voice_ids" in request.params:
        raw = request.params["voice_ids"]
        if not isinstance(raw, list):
            raise ValidationError(f"{label} 的 voice_ids 必须是字符串列表")
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise ValidationError(f"{label} 的 voice_ids 含有空项")
            text = item.strip()
            if text not in found:
                found.append(text)
    if len(found) > 2:
        raise ValidationError(f"{label} 最多 2 个音色,当前 {len(found)} 个")
    return found


def _unit_float(request: GenerationRequest, key: str, default: float, label: str) -> float:
    if key not in request.params:
        return default
    raw = request.params[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValidationError(f"{label} 的 {key} 必须是 0~1 的数字")
    if isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise ValidationError(f"{label} 的 {key} 必须是 0~1 的数字") from exc
    else:
        value = float(raw)
    if value < 0 or value > 1:
        raise ValidationError(f"{label} 的 {key} 须在 0~1,当前 {value}")
    return value


def _expansion(request: GenerationRequest, key: str, label: str) -> float:
    if key not in request.params:
        return 0.1
    raw = request.params[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValidationError(f"{label} 的 {key} 必须是 0~2 的数字")
    if isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise ValidationError(f"{label} 的 {key} 必须是 0~2 的数字") from exc
    else:
        value = float(raw)
    if value < 0 or value > 2:
        raise ValidationError(f"{label} 的 {key} 须在 0~2,当前 {value}")
    return value


def _image_size(request: GenerationRequest, allowed: set[str], label: str) -> str:
    raw = ""
    for key in ("image_size", "resolution"):
        if key not in request.params:
            continue
        value = request.params[key]
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{label} 的 {key} 必须是分辨率档位")
        raw = value.strip().lower()
        break
    if not raw:
        raw = "1k"
    if raw not in allowed:
        shown = ", ".join(sorted(allowed))
        raise ValidationError(f"{label} 不支持画质 {raw},可选: {shown}")
    return raw


def _series_amount(request: GenerationRequest, label: str) -> int | str:
    if "series_amount" not in request.params:
        return 4
    raw = request.params["series_amount"]
    if isinstance(raw, str) and raw.strip().lower() == "auto":
        return "auto"
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的 series_amount 须为 2~9 或 auto")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        raise ValidationError(f"{label} 的 series_amount 须为 2~9 或 auto")
    if value < 2 or value > 9:
        raise ValidationError(f"{label} 的 series_amount 须为 2~9 或 auto,当前 {value}")
    return value


def _options(request: GenerationRequest, label: str) -> dict[str, JsonValue]:
    options: dict[str, JsonValue] = {"watermark_info": {"enabled": bool(request.watermark)}}
    external = _text_param(request, "external_task_id", label)
    callback = _text_param(request, "callback_url", label)
    if external:
        options["external_task_id"] = external
    if callback:
        options["callback_url"] = callback
    return options


def _legacy_extra(request: GenerationRequest, label: str) -> dict[str, JsonValue]:
    extra: dict[str, JsonValue] = {"watermark_info": {"enabled": bool(request.watermark)}}
    external = _text_param(request, "external_task_id", label)
    callback = _text_param(request, "callback_url", label)
    if external:
        extra["external_task_id"] = external
    if callback:
        extra["callback_url"] = callback
    return extra


def _prompt_item(text: str) -> dict[str, JsonValue]:
    return {"type": "prompt", "text": text}


def _frame_item(kind: str, blob: str, index: int) -> dict[str, JsonValue]:
    return {"type": kind, "url": blob, "id": f"image_{index}"}


def _video_item(kind: str, url: str) -> dict[str, JsonValue]:
    return {"type": kind, "url": url, "id": "video_1"}


def _element_item(element_id: str, index: int) -> dict[str, JsonValue]:
    return {"type": "element", "element_id": _element_value(element_id), "id": f"element_{index}"}


def _voice_item(voice_id: str, index: int) -> dict[str, JsonValue]:
    return {"type": "voice", "voice_id": voice_id, "id": f"voice_{index}"}


def _submit(
    *,
    path: str,
    poll_token: str,
    body: dict[str, JsonValue],
    kind: str,
    prompt: str,
    yuan: Decimal,
) -> KlingSubmit:
    return KlingSubmit(path=path, poll_token=poll_token, body=body, kind=kind, prompt=prompt, yuan=yuan)


def _prepare_video(model: str, request: GenerationRequest, *, for_estimate: bool) -> KlingSubmit:
    label = _title(model)
    _reject_ordered_media(request, label)
    if request.audio_refs:
        raise ValidationError(f"{label} 不支持参考音频")
    limit = 3072 if model in {"kling-3.0-turbo", "kling-3.0"} else 2500
    prompt = _prompt(request, limit=limit, required=not for_estimate, label=label)
    if model == "kling-3.0-turbo":
        return _video_turbo(request, prompt, label)
    if model == "kling-3.0":
        return _video_30(request, prompt, label)
    if model == "kling-o1":
        return _video_o1(request, prompt, label)
    if model == "kling-2.6":
        return _video_26(request, prompt, label)
    return _video_25(request, prompt, label)


def _video_turbo(request: GenerationRequest, prompt: str, label: str) -> KlingSubmit:
    if request.video_refs:
        raise ValidationError(f"{label} 不支持参考视频")
    if _element_ids(request, label):
        raise ValidationError(f"{label} 不支持主体")
    blobs = _images(request, maximum=1, label=label)
    resolution = _resolution(request, {"720p", "1080p"}, default="720p", label=label)
    duration = _duration(request, set(range(3, 16)), label)
    yuan = video_yuan("kling-3.0-turbo", resolution=resolution, seconds=duration, sound="always", ref_video=False)
    if not blobs:
        ratio = _ratio(request, _VIDEO_RATIO_SET, default="16:9", label=label)
        body: dict[str, JsonValue] = {
            "prompt": prompt,
            "settings": {"resolution": resolution, "aspect_ratio": ratio, "duration": duration},
            "options": _options(request, label),
        }
        return _submit(
            path="/text-to-video/kling-3.0-turbo",
            poll_token=POLL_TASKS,
            body=body,
            kind="video",
            prompt=prompt,
            yuan=yuan,
        )
    contents: list[JsonValue] = [_prompt_item(prompt), _frame_item("first_frame", blobs[0], 1)]
    body = {
        "contents": contents,
        "settings": {"resolution": resolution, "duration": duration},
        "options": _options(request, label),
    }
    return _submit(
        path="/image-to-video/kling-3.0-turbo",
        poll_token=POLL_TASKS,
        body=body,
        kind="video",
        prompt=prompt,
        yuan=yuan,
    )


def _standard_audio(request: GenerationRequest) -> str:
    return "native" if request.generate_audio else "off"


def _video_30(request: GenerationRequest, prompt: str, label: str) -> KlingSubmit:
    blobs = _images(request, maximum=7, label=label)
    videos = _video_urls(request, label)
    elements = _element_ids(request, label)
    resolution = _resolution(request, {"720p", "1080p", "4k"}, default="720p", label=label)
    duration = _duration(request, set(range(3, 16)), label)
    force_omni = _bool_param(request, "omni", False, label)
    if videos or elements or len(blobs) > 2 or force_omni:
        return _video_omni(
            model="kling-3.0",
            path="/omni-video/kling-3.0-omni",
            request=request,
            prompt=prompt,
            label=label,
            blobs=blobs,
            videos=videos,
            elements=elements,
            resolution=resolution,
            duration=duration,
            native_audio=True,
        )
    audio = _standard_audio(request)
    multi = _bool_param(request, "multi_shot", True, label)
    sound = "native" if audio == "native" else "silent"
    yuan = video_yuan(
        "kling-3.0",
        resolution=resolution,
        seconds=duration,
        sound=sound,
        ref_video=False,
        omni=False,
    )
    if not blobs:
        ratio = _ratio(request, _VIDEO_RATIO_SET, default="16:9", label=label)
        body: dict[str, JsonValue] = {
            "prompt": prompt,
            "settings": {
                "multi_shot": multi,
                "audio": audio,
                "resolution": resolution,
                "aspect_ratio": ratio,
                "duration": duration,
            },
            "options": _options(request, label),
        }
        return _submit(
            path="/text-to-video/kling-3.0",
            poll_token=POLL_TASKS,
            body=body,
            kind="video",
            prompt=prompt,
            yuan=yuan,
        )
    contents: list[JsonValue] = [_prompt_item(prompt), _frame_item("first_frame", blobs[0], 1)]
    if len(blobs) == 2:
        contents.append(_frame_item("last_frame", blobs[1], 2))
    body = {
        "contents": contents,
        "settings": {
            "multi_shot": multi,
            "audio": audio,
            "resolution": resolution,
            "duration": duration,
        },
        "options": _options(request, label),
    }
    return _submit(
        path="/image-to-video/kling-3.0",
        poll_token=POLL_TASKS,
        body=body,
        kind="video",
        prompt=prompt,
        yuan=yuan,
    )


def _video_o1(request: GenerationRequest, prompt: str, label: str) -> KlingSubmit:
    blobs = _images(request, maximum=7, label=label)
    videos = _video_urls(request, label)
    elements = _element_ids(request, label)
    if request.resolution and request.resolution.strip().lower() == "4k":
        raise ValidationError(f"{label} 不支持 4k")
    resolution = _resolution(request, {"720p", "1080p"}, default="720p", label=label)
    plain = (len(blobs) == 0 and not videos and not elements) or (len(blobs) == 1 and not videos and not elements)
    duration = _duration(request, {5, 10} if plain else set(range(3, 11)), label)
    return _video_omni(
        model="kling-o1",
        path="/omni-video/kling-o1",
        request=request,
        prompt=prompt,
        label=label,
        blobs=blobs,
        videos=videos,
        elements=elements,
        resolution=resolution,
        duration=duration,
        native_audio=False,
    )


def _video_omni(
    *,
    model: str,
    path: str,
    request: GenerationRequest,
    prompt: str,
    label: str,
    blobs: list[str],
    videos: list[str],
    elements: list[str],
    resolution: str,
    duration: int,
    native_audio: bool,
) -> KlingSubmit:
    role = ""
    if videos:
        role = _choice(request, "video_role", {"feature", "base"}, "feature", label)
        if role == "base" and blobs:
            raise ValidationError(f"{label} 的待编辑视频不能同时传首帧或尾帧")
        if role == "feature" and len(blobs) > 1:
            raise ValidationError(f"{label} 的特征参考视频只支持首帧,不能带尾帧")
    if len(blobs) == 2 and elements:
        raise ValidationError(f"{label} 使用首尾帧时不能再传主体")
    cap = 4 if videos else 7
    if len(blobs) + len(elements) > cap:
        raise ValidationError(f"{label} 参考图与主体合计最多 {cap} 个,当前 {len(blobs) + len(elements)} 个")

    explicit_multi = "multi_shot" in request.params
    multi: bool | None
    if videos and role == "feature" and native_audio:
        if explicit_multi and not _bool_param(request, "multi_shot", True, label):
            raise ValidationError(f"{label} 的特征参考视频必须开启多镜头")
        # 特征参考视频没有有声档,默认的有声开关不能把请求打成 400。
        audio = "off"
        multi = True
    elif videos and role == "base" and native_audio:
        if explicit_multi and _bool_param(request, "multi_shot", True, label):
            raise ValidationError(f"{label} 的待编辑视频不支持多镜头")
        audio = "original" if request.generate_audio else "off"
        multi = False
    elif native_audio:
        audio = "native" if request.generate_audio else "off"
        multi = _bool_param(request, "multi_shot", True, label)
    else:
        audio = "original" if videos and request.generate_audio else "off"
        multi = None

    contents: list[JsonValue] = [_prompt_item(prompt)]
    if len(blobs) == 1 or len(blobs) >= 3:
        contents.append(_frame_item("first_frame", blobs[0], 1))
        for offset, blob in enumerate(blobs[1:], start=2):
            contents.append(_frame_item("refer_image", blob, offset))
    elif len(blobs) == 2:
        contents.append(_frame_item("first_frame", blobs[0], 1))
        contents.append(_frame_item("last_frame", blobs[1], 2))
    if videos:
        kind = "base_video" if role == "base" else "feature_video"
        contents.append(_video_item(kind, videos[0]))
    for index, element_id in enumerate(elements, start=1):
        contents.append(_element_item(element_id, index))

    settings: dict[str, JsonValue] = {"audio": audio, "resolution": resolution, "duration": duration}
    if multi is not None:
        settings["multi_shot"] = multi
    if not blobs and not videos:
        settings["aspect_ratio"] = _ratio(request, _VIDEO_RATIO_SET, default="16:9", label=label)
    elif request.ratio and not blobs and videos:
        # 有参考视频时纵横比不是必填,调用方传了才带上。
        settings["aspect_ratio"] = _ratio(request, _VIDEO_RATIO_SET, default="16:9", label=label)

    sound = "native" if audio == "native" else "silent"
    yuan = video_yuan(
        model,
        resolution=resolution,
        seconds=duration,
        sound=sound,
        ref_video=bool(videos),
        omni=model == "kling-3.0",
    )
    body: dict[str, JsonValue] = {"contents": contents, "settings": settings, "options": _options(request, label)}
    return _submit(path=path, poll_token=POLL_TASKS, body=body, kind="video", prompt=prompt, yuan=yuan)


def _video_26(request: GenerationRequest, prompt: str, label: str) -> KlingSubmit:
    if request.video_refs:
        raise ValidationError(f"{label} 不支持参考视频")
    if _element_ids(request, label):
        raise ValidationError(f"{label} 不支持主体")
    blobs = _images(request, maximum=2, label=label)
    voices = _voice_ids(request, label)
    duration = _duration(request, {5, 10}, label)
    explicit = (request.resolution or "").strip().lower()
    if len(blobs) == 2:
        if request.generate_audio or voices:
            raise ValidationError(f"{label} 首尾帧只支持 1080p,并且不能开声音或音色")
        if explicit and explicit != "1080p":
            raise ValidationError(f"{label} 首尾帧只支持 1080p")
        resolution = "1080p"
        audio = "off"
        sound = "silent"
    elif request.generate_audio or voices:
        if not blobs and voices:
            raise ValidationError(f"{label} 的音色只支持图生视频")
        if explicit == "720p":
            raise ValidationError(f"{label} 有声只支持 1080p")
        resolution = (
            "1080p"
            if explicit in {"", "1080p"}
            else _resolution(request, {"720p", "1080p"}, default="1080p", label=label)
        )
        if resolution != "1080p":
            raise ValidationError(f"{label} 有声只支持 1080p")
        audio = "native"
        sound = "voice" if voices else "native"
    else:
        resolution = _resolution(request, {"720p", "1080p"}, default="720p", label=label)
        audio = "off"
        sound = "silent"
    yuan = video_yuan("kling-2.6", resolution=resolution, seconds=duration, sound=sound, ref_video=False)
    if not blobs:
        ratio = _ratio(request, _VIDEO_RATIO_SET, default="16:9", label=label)
        body: dict[str, JsonValue] = {
            "prompt": prompt,
            "settings": {"audio": audio, "resolution": resolution, "aspect_ratio": ratio, "duration": duration},
            "options": _options(request, label),
        }
        return _submit(
            path="/text-to-video/kling-2.6",
            poll_token=POLL_TASKS,
            body=body,
            kind="video",
            prompt=prompt,
            yuan=yuan,
        )
    contents: list[JsonValue] = [_prompt_item(prompt), _frame_item("first_frame", blobs[0], 1)]
    if len(blobs) == 2:
        contents.append(_frame_item("last_frame", blobs[1], 2))
    for index, voice_id in enumerate(voices, start=1):
        contents.append(_voice_item(voice_id, index))
    body = {
        "contents": contents,
        "settings": {"audio": audio, "resolution": resolution, "duration": duration},
        "options": _options(request, label),
    }
    return _submit(
        path="/image-to-video/kling-2.6",
        poll_token=POLL_TASKS,
        body=body,
        kind="video",
        prompt=prompt,
        yuan=yuan,
    )


def _video_25(request: GenerationRequest, prompt: str, label: str) -> KlingSubmit:
    if request.video_refs:
        raise ValidationError(f"{label} 不支持参考视频")
    if _element_ids(request, label):
        raise ValidationError(f"{label} 不支持主体")
    blobs = _images(request, maximum=2, label=label)
    duration = _duration(request, {5, 10}, label)
    explicit = (request.resolution or "").strip().lower()
    if len(blobs) == 2:
        if explicit and explicit != "1080p":
            raise ValidationError(f"{label} 首尾帧只支持 1080p")
        resolution = "1080p"
    else:
        resolution = _resolution(request, {"720p", "1080p"}, default="720p", label=label)
    yuan = video_yuan("kling-2.5-turbo", resolution=resolution, seconds=duration, sound="silent", ref_video=False)
    if not blobs:
        ratio = _ratio(request, _VIDEO_RATIO_SET, default="16:9", label=label)
        body: dict[str, JsonValue] = {
            "prompt": prompt,
            "settings": {"resolution": resolution, "aspect_ratio": ratio, "duration": duration},
            "options": _options(request, label),
        }
        path = "/text-to-video/kling-2.5-turbo"
    else:
        contents: list[JsonValue] = [_prompt_item(prompt), _frame_item("first_frame", blobs[0], 1)]
        if len(blobs) == 2:
            contents.append(_frame_item("last_frame", blobs[1], 2))
        body = {
            "contents": contents,
            "settings": {"resolution": resolution, "duration": duration},
            "options": _options(request, label),
        }
        path = "/image-to-video/kling-2.5-turbo"
    return _submit(path=path, poll_token=POLL_TASKS, body=body, kind="video", prompt=prompt, yuan=yuan)


def _prepare_image(model: str, request: GenerationRequest, *, for_estimate: bool) -> KlingSubmit:
    label = _title(model)
    _reject_ordered_media(request, label)
    if request.video_refs or request.audio_refs:
        raise ValidationError(f"{label} 不支持视频或音频参考")
    if model == "kling-image-common":
        return _image_common(request, label, for_estimate=for_estimate)
    if model == "kling-image-2.1":
        return _image_21(request, label, for_estimate=for_estimate)
    if model == "kling-image-o1":
        return _image_omni_model(request, label, for_estimate=for_estimate)
    return _image_30(request, label, for_estimate=for_estimate)


def _image_30(request: GenerationRequest, label: str, *, for_estimate: bool) -> KlingSubmit:
    blobs = _images(request, maximum=10, label=label)
    elements = _element_ids(request, label)
    size = _image_size(request, {"1k", "2k", "4k"}, label)
    ratio = _ratio(request, _IMAGE_RATIO_AUTO, default="16:9", label=label)
    result_type = _choice(request, "result_type", {"single", "series"}, "single", label)
    if len(blobs) > 1 or size == "4k" or result_type == "series" or ratio == "auto" or elements:
        if result_type == "single" and not blobs and ratio == "auto":
            raise ValidationError(f"{label} 文生图单张不支持宽高比 auto")
        return _image_omni(
            request,
            label,
            vendor_model="kling-v3-omni",
            blobs=blobs,
            elements=elements,
            size=size,
            ratio=ratio,
            result_type=result_type,
            for_estimate=for_estimate,
            allow_series=True,
        )
    if ratio == "auto":
        raise ValidationError(f"{label} 文生图不支持宽高比 auto")
    return _image_generations(
        request,
        label,
        vendor_model="kling-v3",
        blobs=blobs,
        elements=elements,
        size=size,
        ratio=ratio,
        for_estimate=for_estimate,
        allow_reference=False,
    )


def _image_omni_model(request: GenerationRequest, label: str, *, for_estimate: bool) -> KlingSubmit:
    blobs = _images(request, maximum=10, label=label)
    elements = _element_ids(request, label)
    size = _image_size(request, {"1k", "2k"}, label)
    ratio = _ratio(request, _IMAGE_RATIO_AUTO, default="16:9", label=label)
    result_type = _choice(request, "result_type", {"single", "series"}, "single", label)
    if result_type == "series":
        raise ValidationError(f"{label} 不支持组图")
    return _image_omni(
        request,
        label,
        vendor_model="kling-image-o1",
        blobs=blobs,
        elements=elements,
        size=size,
        ratio=ratio,
        result_type="single",
        for_estimate=for_estimate,
        allow_series=False,
    )


def _image_generations(
    request: GenerationRequest,
    label: str,
    *,
    vendor_model: str,
    blobs: list[str],
    elements: list[str],
    size: str,
    ratio: str,
    for_estimate: bool,
    allow_reference: bool,
) -> KlingSubmit:
    if len(blobs) > 1:
        raise ValidationError(f"{label} 这一路只接受 1 张参考图")
    if len(elements) + len(blobs) > 10:
        raise ValidationError(f"{label} 参考图与主体合计最多 10 个")
    prompt = _prompt(request, limit=2500, required=not for_estimate, label=label)
    negative = request.negative_prompt or ""
    if blobs and negative.strip():
        raise ValidationError(f"{label} 图生图不支持负向提示词")
    if len(negative) > 2500:
        raise ValidationError(f"{label} 负向提示词最长 2500 个字符")
    reference = _choice(request, "image_reference", {"", "subject", "face"}, "", label)
    if reference and not allow_reference:
        raise ValidationError(f"{label} 不支持角色或长相参考")
    if reference and not blobs:
        raise ValidationError(f"{label} 使用参考类型时必须上传图片")
    count = _int_param(request, "n", 1, 1, 9, label)
    body: dict[str, JsonValue] = {
        "model_name": vendor_model,
        "prompt": prompt,
        "resolution": size,
        "n": count,
        "aspect_ratio": ratio,
    }
    if negative.strip():
        body["negative_prompt"] = negative
    if blobs:
        body["image"] = blobs[0]
    if reference:
        body["image_reference"] = reference
        body["image_fidelity"] = _unit_float(request, "image_fidelity", 0.5, label)
        if reference == "subject":
            body["human_fidelity"] = _unit_float(request, "human_fidelity", 0.45, label)
    if elements:
        body["element_list"] = [{"element_id": _element_value(item)} for item in elements]
    body.update(_legacy_extra(request, label))
    tier = "i2i" if blobs else "t2i"
    priced_as = "kling-image-2.1" if vendor_model == "kling-v2-1" else "kling-image-3.0"
    yuan = image_yuan(priced_as, tier=tier, resolution=size, count=count)
    return _submit(
        path="/v1/images/generations",
        poll_token=POLL_IMAGE_GENERATIONS,
        body=body,
        kind="image",
        prompt=prompt,
        yuan=yuan,
    )


def _image_omni(
    request: GenerationRequest,
    label: str,
    *,
    vendor_model: str,
    blobs: list[str],
    elements: list[str],
    size: str,
    ratio: str,
    result_type: str,
    for_estimate: bool,
    allow_series: bool,
) -> KlingSubmit:
    if result_type == "series" and not allow_series:
        raise ValidationError(f"{label} 不支持组图")
    if len(blobs) + len(elements) > 10:
        raise ValidationError(f"{label} 参考图与主体合计最多 10 个")
    prompt = _prompt(request, limit=2500, required=not for_estimate, label=label)
    body: dict[str, JsonValue] = {
        "model_name": vendor_model,
        "prompt": prompt,
        "resolution": size,
        "aspect_ratio": ratio,
        "result_type": result_type,
    }
    if blobs:
        body["image_list"] = [{"image": blob} for blob in blobs]
    if elements:
        body["element_list"] = [{"element_id": _element_value(item)} for item in elements]
    if result_type == "series":
        amount = _series_amount(request, label)
        body["series_amount"] = amount
        count = 4 if amount == "auto" or not isinstance(amount, int) else amount
    else:
        count = _int_param(request, "n", 1, 1, 9, label)
        body["n"] = count
    body.update(_legacy_extra(request, label))
    model_key = "kling-image-o1" if vendor_model == "kling-image-o1" else "kling-image-3.0"
    yuan = image_yuan(model_key, tier="omni", resolution=size, count=count)
    return _submit(
        path="/v1/images/omni-image",
        poll_token=POLL_IMAGE_OMNI,
        body=body,
        kind="image",
        prompt=prompt,
        yuan=yuan,
    )


def _image_21(request: GenerationRequest, label: str, *, for_estimate: bool) -> KlingSubmit:
    blobs = _images(request, maximum=4, label=label)
    if _element_ids(request, label):
        raise ValidationError(f"{label} 不支持主体 ID,多图请直接传参考图")
    size = _image_size(request, {"1k", "2k"}, label)
    ratio = _ratio(request, _IMAGE_RATIO_SET, default="16:9", label=label)
    if len(blobs) >= 2:
        reference = _choice(request, "image_reference", {"", "subject", "face"}, "", label)
        if reference:
            raise ValidationError(f"{label} 多图参考不支持角色或长相参考")
        prompt = _prompt(request, limit=2500, required=False, label=label)
        count = _int_param(request, "n", 1, 1, 9, label)
        listed: list[JsonValue] = [{"subject_image": blob} for blob in blobs]
        body: dict[str, JsonValue] = {
            "model_name": "kling-v2-1",
            "subject_image_list": listed,
            "n": count,
            "aspect_ratio": ratio,
        }
        if prompt.strip():
            body["prompt"] = prompt
        yuan = image_yuan("kling-image-2.1", tier="multi", resolution=size, count=count)
        return _submit(
            path="/v1/images/multi-image2image",
            poll_token=POLL_IMAGE_MULTI,
            body=body,
            kind="image",
            prompt=prompt,
            yuan=yuan,
        )
    return _image_generations(
        request,
        label,
        vendor_model="kling-v2-1",
        blobs=blobs,
        elements=[],
        size=size,
        ratio=ratio,
        for_estimate=for_estimate,
        allow_reference=True,
    )


def _image_common(request: GenerationRequest, label: str, *, for_estimate: bool) -> KlingSubmit:
    mode = _choice(request, "task_mode", {"expand", "subject"}, "expand", label)
    blobs = _images(request, maximum=1, label=label)
    if len(blobs) != 1:
        action = "主体补全" if mode == "subject" else "扩图"
        raise ValidationError(f"{label} 的{action}需要 1 张图片")
    if mode == "subject":
        body: dict[str, JsonValue] = {"element_frontal_image": blobs[0]}
        external = _text_param(request, "external_task_id", label)
        callback = _text_param(request, "callback_url", label)
        if external:
            body["external_task_id"] = external
        if callback:
            body["callback_url"] = callback
        yuan = image_yuan("kling-image-common", tier="subject", resolution="1k", count=1)
        return _submit(
            path="/v1/general/ai-multi-shot",
            poll_token=POLL_IMAGE_SUBJECT,
            body=body,
            kind="image",
            prompt="",
            yuan=yuan,
        )
    up = _expansion(request, "up_expansion_ratio", label)
    down = _expansion(request, "down_expansion_ratio", label)
    left = _expansion(request, "left_expansion_ratio", label)
    right = _expansion(request, "right_expansion_ratio", label)
    if (1 + up + down) * (1 + left + right) > 3:
        raise ValidationError(f"{label} 扩图新增面积不能超过原图的 3 倍")
    prompt = _prompt(request, limit=2500, required=False, label=label)
    count = _int_param(request, "n", 1, 1, 9, label)
    body = {
        "image": blobs[0],
        "up_expansion_ratio": up,
        "down_expansion_ratio": down,
        "left_expansion_ratio": left,
        "right_expansion_ratio": right,
        "n": count,
    }
    if prompt.strip():
        body["prompt"] = prompt
    body.update(_legacy_extra(request, label))
    yuan = image_yuan("kling-image-common", tier="outpaint", resolution="1k", count=count)
    return _submit(
        path="/v1/images/editing/expand",
        poll_token=POLL_IMAGE_EXPAND,
        body=body,
        kind="image",
        prompt=prompt,
        yuan=yuan,
    )


__all__ = ["KlingSubmit", "prepare"]
