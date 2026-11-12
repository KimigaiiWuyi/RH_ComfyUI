"""fal MiniMax H3-Image 组包:text-to-image / edit 两端点。

与官方 MiniMax-H3-Image 同源(模型×供应商正交,模型级计费曲线不动):
- 文生图 → minimax/h3-image/text-to-image(image_size 必填显式,无 auto)
- 传图即编辑 → minimax/h3-image/edit(image_urls ≤16;image_size 可 "auto"
  = 跟随图 1 宽高比,限定 2048×2048 面积内)
fal 表达不了的参数抛 ValidationError,由通道转 CAPABILITY 换源:
auto 宽高比文生图、watermark、seed、highspeed 变体。
"""

from __future__ import annotations

import re
from math import sqrt
from decimal import Decimal

from .catalog import (
    APP_PREFIXES,
    MMH3_IMAGE_FORMATS,
    MMH3_IMAGE_MAX_EDGE,
    MMH3_IMAGE_ENDPOINTS,
    MMH3_IMAGE_MAX_IMAGES,
    MMH3_IMAGE_TIER_PIXELS,
)
from .payload import FalSubmit, JsonValue, _text_param
from .payload_h3 import _inputs, _bool_param, _number_param
from ...core.types import MediaKind
from ....core.base.errors import ValidationError
from ....utils.core.request import GenerationRequest

_LABEL = "MiniMax H3-Image (fal)"
# OC 媒体代号 → fal edit 约定的 "Image N" 引用写法
_REF_MARKER = re.compile(r"\[@?参考图片(\d+)\s*\]")
# fal 显式 {width,height} 边界:面积 / 宽高比
_AREA_MIN = 655_360
_AREA_MAX = 20_000_000
_RATIO_MIN = 1.0 / 3.0
_RATIO_MAX = 3.0


def _explicit_size(request: GenerationRequest) -> tuple[int, int] | None:
    """params['size'] 'WxH' 或 params 显式 width+height → (w,h)。"""
    size_raw = _text_param(request, "size", label=_LABEL)
    if size_raw:
        parts = size_raw.lower().split("x")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValidationError(f"{_LABEL} 的 size 必须是 '宽x高' 像素串,实际收到 {size_raw!r}")
        return int(parts[0]), int(parts[1])
    w_raw = request.params["width"] if "width" in request.params else None
    h_raw = request.params["height"] if "height" in request.params else None
    if w_raw is None or h_raw is None:
        return None
    try:
        w = int(str(w_raw))
        h = int(str(h_raw))
    except (TypeError, ValueError):
        raise ValidationError(f"{_LABEL} 的 width/height 必须是整数像素") from None
    return w, h


def _check_bounds(w: int, h: int) -> None:
    area = w * h
    if area < _AREA_MIN or area > _AREA_MAX:
        raise ValidationError(f"{_LABEL} 的 size 像素面积须为 {_AREA_MIN}~{_AREA_MAX},当前 {w}x{h}")
    r = w / h
    if r < _RATIO_MIN or r > _RATIO_MAX:
        raise ValidationError(f"{_LABEL} 的 size 宽高比须为 1:3~3:1,当前 {w}x{h}")


def _ratio(request: GenerationRequest, *, is_edit: bool = False) -> str:
    raw = _text_param(request, "ratio", label=_LABEL).lower()
    if not raw and isinstance(request.ratio, str):
        raw = request.ratio.strip().lower()
    if not raw:
        return "auto" if is_edit else "1:1"
    if raw == "auto":
        return "auto"
    if ":" not in raw:
        raise ValidationError(f"{_LABEL} 的宽高比非法:{raw!r};须为 'auto' 或 '宽:高'")
    wr, hr = raw.split(":", 1)
    if not wr.isdigit() or not hr.isdigit() or int(wr) <= 0 or int(hr) <= 0:
        raise ValidationError(f"{_LABEL} 的宽高比非法:{raw!r}")
    r = int(wr) / int(hr)
    if r < _RATIO_MIN or r > _RATIO_MAX:
        raise ValidationError(f"{_LABEL} 的宽高比 {raw!r} 超出 1:3~3:1 范围")
    return f"{int(wr)}:{int(hr)}"


def _tier(request: GenerationRequest) -> str:
    raw = _text_param(request, "image_size", label=_LABEL).upper() or "2K"
    if raw not in MMH3_IMAGE_TIER_PIXELS:
        raise ValidationError(f"{_LABEL} 的 image_size 非法:{raw!r};可选值:{'/'.join(MMH3_IMAGE_TIER_PIXELS)}")
    return raw


def _dims(ratio: str, tier: str) -> dict[str, JsonValue]:
    """'W:H' × 档位像素 → fal {width,height}:16 倍数、最长边 ≤7745、比例 ≤3:1。"""
    wr, hr = (float(p) for p in ratio.split(":"))
    r = wr / hr
    area = float(MMH3_IMAGE_TIER_PIXELS[tier])
    w = min(int(sqrt(area * r) // 16) * 16, MMH3_IMAGE_MAX_EDGE)
    h = min(int(sqrt(area / r) // 16) * 16, MMH3_IMAGE_MAX_EDGE)
    w, h = max(w, 16), max(h, 16)
    # 16 取整会让比例轻微出界,逐格钳回 3:1 内
    while w / h > _RATIO_MAX and w > 16:
        w -= 16
    while h / w > _RATIO_MAX and h > 16:
        h -= 16
    return {"width": w, "height": h}


def _variant(request: GenerationRequest) -> None:
    raw = _text_param(request, "model_variant", label=_LABEL).lower()
    if raw in ("", "standard", "std", "default"):
        return
    if raw in ("highspeed", "high_speed", "fast", "turbo"):
        raise ValidationError(f"{_LABEL} 只承载标准版 MiniMax-H3-Image,highspeed 请走官方通道")
    raise ValidationError(f"{_LABEL} 的 model_variant 非法:{raw!r};可选值:standard/highspeed")


def prepare_minimax_h3_image(model_name: str, request: GenerationRequest, *, for_estimate: bool = False) -> FalSubmit:
    """校验 + 组装 fal H3-Image 请求体(不打网络)。"""
    prompt = (request.prompt or "").strip()
    if not prompt and not for_estimate:
        raise ValidationError(f"{_LABEL} 需要提示词")
    if request.video_refs or request.audio_refs:
        raise ValidationError(f"{_LABEL} 是生图模型,不接受音视频参考素材")
    _variant(request)
    if request.watermark or _bool_param(request, "watermark", False, label=_LABEL):
        raise ValidationError(f"{_LABEL} 不支持 aigc 水印,请走官方通道")
    if request.seed is not None:
        raise ValidationError(f"{_LABEL} 不支持 seed 参数")

    # OC 与扁平统一走 _inputs:URL 透传、bytes 转 data URI、文本段并入 prompt
    collected = _inputs(request, label=_LABEL)
    uris: list[JsonValue] = []
    for m in collected.media:
        if m.kind != MediaKind.IMAGE:
            raise ValidationError(f"{_LABEL} 只接受图片素材,实际收到 {m.kind.value}")
        uris.append(m.uri)
    if collected.extra_text:
        prompt = f"{prompt}\n{' '.join(collected.extra_text)}".strip()
    if len(uris) > MMH3_IMAGE_MAX_IMAGES:
        raise ValidationError(f"{_LABEL} 最多 {MMH3_IMAGE_MAX_IMAGES} 张参考图,当前 {len(uris)} 张")
    is_edit = bool(uris)
    fmt = _text_param(request, "output_format", label=_LABEL).lower() or "jpeg"
    if fmt not in MMH3_IMAGE_FORMATS:
        raise ValidationError(f"{_LABEL} 的 output_format 非法:{fmt!r};可选值:{'/'.join(MMH3_IMAGE_FORMATS)}")

    if is_edit:
        # edit prompt 约定 "Image 1, Image 2, ..." 引用 image_urls 顺序
        prompt = _REF_MARKER.sub(lambda m: f"Image {m.group(1)}", prompt)

    body: dict[str, JsonValue] = {
        "prompt": prompt or "(estimate)",
        "num_images": 1,
        "output_format": fmt,
    }
    if is_edit:
        body["image_urls"] = uris

    size = _explicit_size(request)
    if size is not None:
        _check_bounds(*size)
        body["image_size"] = {"width": size[0], "height": size[1]}
    else:
        ratio = _ratio(request, is_edit=is_edit)
        if ratio == "auto":
            if is_edit:
                body["image_size"] = "auto"
            else:
                raise ValidationError(
                    f"{_LABEL} 文生图不支持 auto 宽高比,请选具体比例或 size 像素尺寸(官方通道可 auto)"
                )
        else:
            body["image_size"] = _dims(ratio, _tier(request))

    if not is_edit:
        body["enable_prompt_expansion"] = _bool_param(request, "prompt_enhanced", True, label=_LABEL)
    num = _number_param(request, "n", label=_LABEL)
    if num is not None and num != 1:
        raise ValidationError(f"{_LABEL} 单次返回 1 张图,不支持 n={num}")

    mode = "edit" if is_edit else "text_to_image"
    return FalSubmit(
        path=f"/{MMH3_IMAGE_ENDPOINTS[mode]}",
        app=APP_PREFIXES[model_name],
        body=body,
        prompt=prompt,
        usd=Decimal(0),
        mode=mode,
        output="image",
    )


__all__ = ["prepare_minimax_h3_image"]
