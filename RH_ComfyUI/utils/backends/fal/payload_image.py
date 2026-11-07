"""fal 图像端点组包:banana 家族 + gpt-image-2。

插件已有模型挂到 fal 通道(模型×供应商正交,模型级计费曲线不动):
- banana1/2/2.1/2.lite/banana_pro → owner/app[/edit],传图即走 /edit;
- gpt-image-2 → openai/gpt-image-2[/edit]。
fal 表达不了的参数(gpt-image quality=xhigh/max、output_format=gif、ratio=auto
以外的 image_size 组合约束)抛 ValidationError,由通道转 CAPABILITY 换源。
"""

from __future__ import annotations

import base64
from math import sqrt
from decimal import Decimal

from .catalog import (
    APP_PREFIXES,
    BANANA_FAL_THINKING,
    BANANA_FAL_ENDPOINTS,
    BANANA_FAL_RESOLUTIONS,
    GPT_IMAGE_FAL_ENDPOINT,
    GPT_IMAGE_FAL_MAX_EDGE,
    GPT_IMAGE_FAL_TIER_PIXELS,
)
from .payload import FalSubmit, JsonValue, _label, _text_param
from .payload_h3 import _inputs
from ...core.types import MediaKind, sniff_mime_for_kind_known
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

# fal 各 banana 端点收的宽高比全集(nano-banana-pro / 2 / 2.1 / lite 一致,1 代子集)
_BANANA_RATIOS = {
    "banana1": ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "4:5", "5:4"),
    "banana2": (
        "auto",
        "21:9",
        "16:9",
        "3:2",
        "4:3",
        "5:4",
        "1:1",
        "4:5",
        "3:4",
        "2:3",
        "9:16",
        "4:1",
        "1:4",
        "8:1",
        "1:8",
    ),
    "banana2.1": (
        "auto",
        "21:9",
        "16:9",
        "3:2",
        "4:3",
        "5:4",
        "1:1",
        "4:5",
        "3:4",
        "2:3",
        "9:16",
        "4:1",
        "1:4",
        "8:1",
        "1:8",
    ),
    "banana2.lite": (
        "auto",
        "21:9",
        "16:9",
        "3:2",
        "4:3",
        "5:4",
        "1:1",
        "4:5",
        "3:4",
        "2:3",
        "9:16",
        "4:1",
        "1:4",
        "8:1",
        "1:8",
    ),
    "banana_pro": (
        "auto",
        "21:9",
        "16:9",
        "3:2",
        "4:3",
        "5:4",
        "1:1",
        "4:5",
        "3:4",
        "2:3",
        "9:16",
        "4:1",
        "1:4",
        "8:1",
        "1:8",
    ),
}
# 插件产品面的 8:5(图片包 640×400)就近折成 3:2,与 Gemini mapper 同口径
_RATIO_FOLD = {"8:5": "3:2"}
_GPT_QUALITIES = ("low", "medium", "high")
_GPT_BACKGROUNDS = ("auto", "transparent", "opaque")
_GPT_FORMATS = ("png", "jpeg")
_GPT_RATIOS = ("1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4", "21:9")


def _image_uris(request: GenerationRequest, *, label: str) -> list[JsonValue]:
    """扁平 images / OC 图片项 → data URI 列表;非图片素材报错。"""
    uris: list[JsonValue] = []
    if request.ordered_content:
        inputs = _inputs(request, label=label)
        for m in inputs.media:
            if m.kind != MediaKind.IMAGE:
                raise ValidationError(f"{label} 只接受图片素材,实际收到 {m.kind.value}")
            uris.append(m.uri)
        return uris
    for blob in request.images:
        if not blob:
            raise ValidationError(f"{label} 的参考图是空的")
        mime = sniff_mime_for_kind_known(blob, MediaKind.IMAGE) or "image/png"
        uris.append(f"data:{mime};base64,{base64.b64encode(blob).decode('ascii')}")
    return uris


def _ratio(request: GenerationRequest, allowed: tuple[str, ...], default: str, *, label: str) -> str:
    raw = _text_param(request, "ratio", label).lower()
    if not raw and isinstance(request.ratio, str):
        raw = request.ratio.strip().lower()
    if not raw:
        raw = default
    raw = _RATIO_FOLD.get(raw, raw)
    if raw not in allowed:
        raise ValidationError(f"{label} 的宽高比 {raw!r} 在 fal 通道不支持")
    return raw


def _size_tier(request: GenerationRequest, *, label: str) -> str:
    raw = _text_param(request, "image_size", label).upper()
    if not raw:
        return "1K"
    if raw == "512":
        return "512"
    if raw in ("1K", "2K", "4K"):
        return raw
    raise ValidationError(f"{label} 的 image_size 非法:{raw!r}")


def _gpt_dims(ratio: str, tier: str) -> dict[str, JsonValue]:
    """ratio×档位 → fal {width,height}:16 倍数、最长边 ≤3840、像素 ≈ 档位预算。"""
    w_r, h_r = (float(p) for p in ratio.split(":"))
    if w_r / h_r > 3.0 or h_r / w_r > 3.0:
        raise ValidationError(f"fal 通道的 gpt-image-2 只支持 ≤3:1 的宽高比,实际收到 {ratio}")
    area = float(GPT_IMAGE_FAL_TIER_PIXELS[tier])
    w = min(round(sqrt(area * w_r / h_r) / 16) * 16, GPT_IMAGE_FAL_MAX_EDGE)
    h = min(round(sqrt(area * h_r / w_r) / 16) * 16, GPT_IMAGE_FAL_MAX_EDGE)
    return {"width": max(w, 16), "height": max(h, 16)}


def _prepare_banana(
    model_name: str,
    request: GenerationRequest,
    uris: list[JsonValue],
    prompt: str,
    *,
    for_estimate: bool,
    label: str,
) -> FalSubmit:
    endpoint = BANANA_FAL_ENDPOINTS[model_name]
    body: dict[str, JsonValue] = {"prompt": prompt or "(estimate)", "num_images": 1}
    ratio = _ratio(request, _BANANA_RATIOS[model_name], "auto" if model_name != "banana1" else "1:1", label=label)
    if ratio != "auto":
        body["aspect_ratio"] = ratio
    res_map = BANANA_FAL_RESOLUTIONS[model_name]
    if res_map is not None:
        tier = _size_tier(request, label=label)
        if tier not in res_map:
            raise ValidationError(f"{label} 的 image_size={tier} 在 fal 通道不支持(可选:{'/'.join(res_map)})")
        body["resolution"] = res_map[tier]
    thinking = BANANA_FAL_THINKING[model_name]
    if thinking is not None:
        raw = _text_param(request, "thinking_level", label).lower() or thinking[-1]
        if raw not in thinking:
            raise ValidationError(f"{label} 的 thinking_level={raw} 在 fal 通道不支持(可选:{'/'.join(thinking)})")
        body["thinking_level"] = raw
    if request.seed is not None:
        body["seed"] = request.seed
    if uris:
        endpoint += "/edit"
        body["image_urls"] = uris
    return FalSubmit(
        path=f"/{endpoint}",
        app=APP_PREFIXES[model_name],
        body=body,
        prompt=prompt,
        usd=Decimal(0),
        mode="edit" if uris else "text_to_image",
        output="image",
    )


def _prepare_gpt_image(
    model_name: str,
    request: GenerationRequest,
    uris: list[JsonValue],
    prompt: str,
    *,
    for_estimate: bool,
    label: str,
) -> FalSubmit:
    endpoint = GPT_IMAGE_FAL_ENDPOINT
    body: dict[str, JsonValue] = {"prompt": prompt or "(estimate)", "num_images": 1}
    ratio_raw = _text_param(request, "ratio", label).lower()
    if not ratio_raw and isinstance(request.ratio, str):
        ratio_raw = request.ratio.strip().lower()
    tier = _text_param(request, "image_size", label).upper() or "2K"
    if tier == "512":
        tier = "1K"
    if tier not in GPT_IMAGE_FAL_TIER_PIXELS:
        raise ValidationError(f"{label} 的 image_size={tier} 在 fal 通道不支持")
    if not ratio_raw or ratio_raw == "auto":
        body["image_size"] = "auto" if tier == "1K" else _gpt_dims("1:1", tier)
    else:
        if ratio_raw not in _GPT_RATIOS:
            raise ValidationError(f"{label} 的宽高比 {ratio_raw!r} 在 fal 通道不支持")
        body["image_size"] = _gpt_dims(ratio_raw, tier)
    quality = _text_param(request, "quality", label).lower() or "high"
    if quality not in _GPT_QUALITIES:
        raise ValidationError(f"{label} 的 quality={quality} 在 fal 通道不支持(可选:low/medium/high)")
    body["quality"] = quality
    background = _text_param(request, "background", label).lower() or "auto"
    if background not in _GPT_BACKGROUNDS:
        raise ValidationError(f"{label} 的 background={background} 在 fal 通道不支持")
    body["background"] = background
    fmt = _text_param(request, "output_format", label).lower() or "png"
    if fmt == "jpg":
        fmt = "jpeg"
    if fmt not in _GPT_FORMATS:
        raise ValidationError(f"{label} 的 output_format={fmt} 在 fal 通道不支持(可选:png/jpeg)")
    body["output_format"] = fmt
    if background == "transparent" and fmt == "jpeg":
        raise ValidationError(f"{label} 的 transparent 背景不能配 jpeg")
    if uris:
        endpoint += "/edit"
        body["image_urls"] = uris
    return FalSubmit(
        path=f"/{endpoint}",
        app=APP_PREFIXES[model_name],
        body=body,
        prompt=prompt,
        usd=Decimal(0),
        mode="edit" if uris else "text_to_image",
        output="image",
    )


def prepare_image(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """banana 家族 / gpt-image-2 的校验 + 组包(不打网络)。"""
    label = _label(model_name)
    prompt = (request.prompt or "").strip()
    if not prompt and not for_estimate:
        raise ValidationError(f"{label} 需要提示词")
    if request.video_refs or request.audio_refs:
        raise ValidationError(f"{label} 是生图模型,不接受音视频参考素材")
    uris = _image_uris(request, label=label)
    if model_name == "gpt-image-2":
        return _prepare_gpt_image(model_name, request, uris, prompt, for_estimate=for_estimate, label=label)
    return _prepare_banana(model_name, request, uris, prompt, for_estimate=for_estimate, label=label)
