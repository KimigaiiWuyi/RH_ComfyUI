"""Seedream 5.0 (Lite / Pro) 生图 mapper — 同步 /v1/images/generations

负责把 GenerationRequest 翻译成 Seedream 同步接口的请求体:
- prompt + model 由 NodeDef.backend_model 注入
- 参考图(`request.images` bytes 列表)经 R2 上传后组成 image 字段(string / string[])
- size 由 size_mode 档位 + ratio 宽高比自动计算为像素值(文档「方式 2」),
  不允许混用档位字符串与像素字符串。映射表来自火山方舟 Seedream 5.0 官方文档。
"""

from __future__ import annotations

import base64
import asyncio
from typing import TYPE_CHECKING
from collections.abc import Mapping

from ..core.types import NodeOutput
from ..image_process import encode_pil_image

if TYPE_CHECKING:
    from ..backends.seedream.api import SeedreamAPI


# ── size 映射表(来自 Seedream 5.0 官方文档「方式 2」) ───────────────────
#
# Seedream 5.0 Pro 仅支持 1K/2K 两档;Lite 支持 2K/3K/4K。
# 每个档位下各宽高比对应固定的宽高像素值(总像素需在约束范围内)。


# Pro: 1K/2K
_SEEDREAM_PRO_SIZE_MAP: dict[str, dict[str, str]] = {
    "1K": {
        "1:1": "1024x1024",
        "4:3": "1152x864",
        "3:4": "864x1152",
        "16:9": "1424x800",
        "9:16": "800x1424",
        "3:2": "1248x832",
        "2:3": "832x1248",
        "21:9": "1568x672",
    },
    "2K": {
        "1:1": "2048x2048",
        "4:3": "2368x1776",
        "3:4": "1776x2368",
        "16:9": "2816x1584",
        "9:16": "1584x2816",
        "3:2": "2496x1664",
        "2:3": "1664x2496",
        "21:9": "3136x1344",
    },
}

# Lite: 2K/3K/4K
_SEEDREAM_LITE_SIZE_MAP: dict[str, dict[str, str]] = {
    "2K": {
        "1:1": "2048x2048",
        "4:3": "2304x1728",
        "3:4": "1728x2304",
        "16:9": "2848x1600",
        "9:16": "1600x2848",
        "3:2": "2496x1664",
        "2:3": "1664x2496",
        "21:9": "3136x1344",
    },
    "3K": {
        "1:1": "3072x3072",
        "4:3": "3456x2592",
        "3:4": "2592x3456",
        "16:9": "4096x2304",
        "9:16": "2304x4096",
        "3:2": "3744x2496",
        "2:3": "2496x3744",
        "21:9": "4704x2016",
    },
    "4K": {
        "1:1": "4096x4096",
        "4:3": "4704x3520",
        "3:4": "3520x4704",
        "16:9": "5504x3040",
        "9:16": "3040x5504",
        "3:2": "4992x3328",
        "2:3": "3328x4992",
        "21:9": "6240x2656",
    },
}

# host 模型 → 映射表(Pro 用 Pro 表,Lite 用 Lite 表)
_SEEDREAM_MODEL_SIZE_MAP: dict[str, dict[str, dict[str, str]]] = {
    "seedream5_pro": _SEEDREAM_PRO_SIZE_MAP,
    "seedream5": _SEEDREAM_LITE_SIZE_MAP,
}

# Pro 模型 vendor id 集合(用于判定用哪张表)
_PRO_VENDOR_PREFIXES = ("doubao-seedream-5.0-pro", "seedream-5.0-pro")


def _is_pro_model(vendor_model: str) -> bool:
    """根据 vendor_model id 判断是否为 Pro 档"""
    return any(vendor_model.startswith(p) for p in _PRO_VENDOR_PREFIXES)


def resolve_seedream_size(vendor_model: str, size_mode: str | None, ratio: str | str | None) -> str:
    """size_mode(档位) + ratio(宽高比) → 像素尺寸字符串(如 "2048x2048")

    规则:
    - 根据 model id 选 Pro/Lite 映射表
    - size_mode 缺省默认 "2K"
    - ratio 缺省默认 "1:1"
    - 找不到匹配时回落 "2048x2048"(不会静默传一个无效值给上游)
    """
    size_map = _SEEDREAM_PRO_SIZE_MAP if _is_pro_model(vendor_model) else _SEEDREAM_LITE_SIZE_MAP
    tier = size_mode or "2K"
    ratio_key = ratio or "1:1"
    tier_map = size_map.get(tier)
    if tier_map is None:
        # 档位不匹配(如 Pro 传了 4K),回落 2K
        tier_map = size_map.get("2K", {})
    return tier_map.get(ratio_key, "2048x2048")


def _infer_image_mime(image_bytes: bytes) -> str:
    """根据字节流 mime 魔数猜 mime(默认 jpeg,能识别 png 时切换)"""
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "image/jpeg"


def _encode_images_to_data_urls(images: list[bytes]) -> list[str]:
    """bytes 列表 → data URL 列表(ARK image 字段备用形态)"""
    urls: list[str] = []
    for img_bytes in images:
        if not img_bytes:
            continue
        mime = _infer_image_mime(img_bytes)
        b64 = base64.b64encode(img_bytes).decode("ascii")
        urls.append(f"data:{mime};base64,{b64}")
    return urls


async def seedream_mapper(request, api: "SeedreamAPI") -> NodeOutput:
    """Seedream 50 Lite / Pro 的统一 mapper(同步 /v1/images/generations)

    request.params 中由 NodeDef 注入:
      - model:           后端模型 ID(来自 NodeDef.backend_model)
      - size_mode:       分辨率档位(Pro: 1K/2K, Lite: 2K/3K/4K)
      - ratio:           宽高比(1:1/16:9/9:16/4:3/3:4/3:2/2:3/21:9)
      - output_format:   输出格式("png" / "jpeg",默认 png)
      - response_format: "url"(默认)/ "b64_json"
      - watermark:       是否带水印(默认 False)
    """
    model = request.params.get("model") or ""
    if not model:
        raise RuntimeError("Seedream 节点未声明 backend_model")

    size_mode = request.params.get("size_mode")
    ratio = request.params.get("ratio") or request.ratio or None
    output_format = request.params.get("output_format") or "png"
    response_format = request.params.get("response_format") or "url"
    watermark = bool(request.params.get("watermark", False))

    # size: 档位 + 宽高比 → 像素值(方式 2)
    size = resolve_seedream_size(model, size_mode, ratio)

    body: dict = {
        "model": model,
        "prompt": request.prompt,
        "size": size,
        "response_format": response_format,
        "output_format": output_format,
        "watermark": watermark,
    }

    # 参考图:0 张=文生图;1+ 张=图生图/编辑/多参考
    if request.images:
        # mapper 不直接编码 base64,由调用方在 media 层上传 R2 后注入 urls
        image_urls = request.params.get("_image_urls")
        if image_urls:
            # 单图传 str,多传 list(与 ARK 文档一致)
            body["image"] = image_urls[0] if len(image_urls) == 1 else image_urls
        else:
            # 没 R2 则回落 data URL(不推荐,可能被 413 拒)
            body["image"] = await asyncio.to_thread(_encode_images_to_data_urls, list(request.images))

    # Pro 不接受 Lite-only 字段(防御性拦截)
    if _is_pro_model(model):
        for pro_blocked in ("sequential_image_generation", "sequential_image_generation_options", "tools"):
            if pro_blocked in request.params:
                raise RuntimeError(f"Seedream 5.0 Pro 不支持字段 {pro_blocked},请改用 Seedream 5.0 Lite")
    else:
        # Lite 默认走 disabled(单图);调用方可通过 params["sequential_image_generation"] = "auto" 显式开启
        seq = request.params.get("sequential_image_generation")
        if seq in ("auto", "disabled"):
            body["sequential_image_generation"] = seq
        max_imgs = request.params.get("max_images")
        if isinstance(max_imgs, int) and 1 <= max_imgs <= 15:
            body["sequential_image_generation_options"] = {"max_images": max_imgs}

    # 提示词优化(standard/fast)
    opt_mode = request.params.get("optimize_mode")
    if opt_mode in ("standard", "fast"):
        body["optimize_prompt_options"] = {"mode": opt_mode}

    # 种子(若调用方显式提供)
    if request.seed is not None:
        body["seed"] = int(request.seed)

    from ...core.telemetry.wire_capture import set_wire_from_http_body

    set_wire_from_http_body(body)
    result = await api.generate(body)

    pil_image = result["image"]
    # PIL 编码是 CPU 重活，下线程；mime 与实际输出格式对齐
    data, mime = await asyncio.to_thread(encode_pil_image, pil_image, output_format)

    return NodeOutput(
        status="ok",
        output_type="image",
        data=data,
        mime_type=mime,
        outputs={"image": data},
        usage={
            "model": result["model"],
            "size": result["size"],
            "generated_images": result["generated_images"],
            "output_format": result["output_format"] or output_format,
            "vendor": "ark",
        },
        raw=result["raw"],
    )


# ═══════════════════════════════════════════════════════════════════════
#  Seedream 5.5(doubao-seedream-5-5-*)
# ═══════════════════════════════════════════════════════════════════════
#
# 与 5.0 的差异:
# - size 直接透传档位串("1K"/"1.5K"/"2K")或显式 "WxH";宽高比按官方推荐
#   以自然语言写进 prompt(无 ratio 字段)。图层拆分场景仅档位或 "auto"。
# - 新增 layer_decomposition(1 底图 + ≤99 图层)、layer_decomposition_options
#   (max_images 2~100 / text_recognition 富文本 OCR)、background(auto/
#   transparent/opaque 原生 RGBA)、safety_identifier。
# - 不支持 sequential_image_generation / stream / tools(校验层拦截)。
# - 图层拆分返回多图:底图作主产物,其余图层进 outputs["layer_{z:02d}"],
#   逐层 z_index/bounding_box/name/description/OCR 进 usage["layers"]。

_SEEDREAM55_TIERS = ("1K", "1.5K", "2K")
_SEEDREAM55_BACKGROUNDS = ("auto", "transparent", "opaque")
_SEEDREAM55_DEFAULT_MAX_IMAGES = 30


def _opt_int(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _truthy(raw: object) -> bool:
    """BOOLEAN 端口兼容字符串形态('false' 不得被 bool() 当成 True)。"""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return isinstance(raw, int) and raw != 0


def _str_or(params: Mapping[str, object], key: str, default: str) -> str:
    raw = params[key] if key in params else None
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


def _norm_tier_55(raw: object, *, allow_auto: bool) -> str:
    """image_size 归一到 1K/1.5K/2K(图层拆分再允许 auto);非法值回落 2K。"""
    t = str(raw or "").strip().upper() or "2K"
    if t == "AUTO":
        return "auto" if allow_auto else "2K"
    return t if t in _SEEDREAM55_TIERS else "2K"


def _resolve_size_55(params: Mapping[str, object], *, layer_mode: bool) -> str:
    """上游 size 取值:params['size'](WxH/档位/auto) > width+height > image_size 档位。

    'WxH' 显式像素仅在图片生成场景有效;图层拆分只透传档位或 auto。
    """
    raw = params["size"] if "size" in params else None
    if isinstance(raw, str):
        s = raw.strip()
        if s.lower() == "auto" and layer_mode:
            return "auto"
        if s.upper() in _SEEDREAM55_TIERS:
            return s.upper()
        parts = s.lower().split("x")
        if not layer_mode and len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}x{parts[1]}"
    w = _opt_int(params["width"] if "width" in params else None)
    h = _opt_int(params["height"] if "height" in params else None)
    if not layer_mode and w is not None and h is not None and w > 0 and h > 0:
        return f"{w}x{h}"
    tier_raw = (
        params["image_size"] if "image_size" in params else (params["size_mode"] if "size_mode" in params else None)
    )
    return _norm_tier_55(tier_raw, allow_auto=layer_mode)


def _layer_manifest_entry(item: object) -> dict[str, object]:
    """从 data[] 条目提取图层清单字段(z_index/bbox/名称/OCR)。"""
    entry: dict[str, object] = {}
    if not isinstance(item, dict):
        return entry
    for key in ("z_index", "name", "description", "size", "output_format", "bounding_box", "text_recognition"):
        if key in item:
            entry[key] = item[key]
    return entry


async def seedream55_mapper(request, api: "SeedreamAPI") -> NodeOutput:
    """Seedream 5.5 mapper(同步 /images/generations)

    request.params 约定(与 NodeDef.inputs 同名):
      - model:                vendor id(backend_model 注入)
      - image_size:           档位 "1K"/"1.5K"/"2K"/"auto"(auto 仅图层拆分;兼容 size_mode)
      - size:                 显式 "WxH"(仅图片生成,优先级高于档位)
      - ratio:                宽高比(档位模式下拼进 prompt;显式 WxH / 图层拆分忽略)
      - layer_decomposition:  图层拆分开关
      - layer_max_images:     最大输出图片数 2~100(默认 30,含底图)
      - text_recognition:     富文本 OCR 返回开关(默认 False)
      - background:           auto/transparent/opaque(仅图片生成;图层拆分禁止)
      - output_format:        "png"(默认)/"jpeg"(图层拆分只控制底图)
      - response_format:      "url"(默认)/"b64_json"
      - watermark:            是否带水印(默认 False)
      - safety_identifier:    终端用户标识(≤64 字符)
    """
    params = request.params
    model = params["model"] if "model" in params else ""
    if not isinstance(model, str) or not model:
        raise RuntimeError("Seedream 5.5 节点未声明 backend_model")

    layer_mode = _truthy(params["layer_decomposition"] if "layer_decomposition" in params else None)
    response_format = _str_or(params, "response_format", "url").lower()
    output_format = _str_or(params, "output_format", "png").lower()
    watermark = _truthy(params["watermark"] if "watermark" in params else None)
    ratio_raw = params["ratio"] if "ratio" in params else None
    ratio = ratio_raw if isinstance(ratio_raw, str) and ratio_raw else request.ratio

    prompt = request.prompt or ""
    size = _resolve_size_55(params, layer_mode=layer_mode)
    # 档位模式下宽高比用自然语言写进 prompt(官方推荐);显式 WxH / 图层拆分忽略
    if not layer_mode and "x" not in size and ratio:
        prompt = f"{prompt},画面宽高比 {ratio}" if prompt else f"画面宽高比 {ratio}"

    body: dict = {
        "model": model,
        "size": size,
        "response_format": response_format,
        "output_format": output_format,
        "watermark": watermark,
    }
    # 图层拆分时 prompt 可选:留空由模型自动识别全部主要元素
    if prompt or not layer_mode:
        body["prompt"] = prompt

    if request.images:
        urls_raw = params["_image_urls"] if "_image_urls" in params else None
        image_urls = [u for u in urls_raw if isinstance(u, str) and u] if isinstance(urls_raw, list) else []
        if image_urls:
            body["image"] = image_urls[0] if len(image_urls) == 1 else image_urls
        else:
            body["image"] = await asyncio.to_thread(_encode_images_to_data_urls, list(request.images))

    if layer_mode:
        max_images = _opt_int(params["layer_max_images"] if "layer_max_images" in params else None)
        if max_images is None:
            max_images = _SEEDREAM55_DEFAULT_MAX_IMAGES
        options: dict[str, object] = {"max_images": max_images}
        if _truthy(params["text_recognition"] if "text_recognition" in params else None):
            options["text_recognition"] = True
        body["layer_decomposition"] = True
        body["layer_decomposition_options"] = options
    else:
        bg = params["background"] if "background" in params else None
        if isinstance(bg, str) and bg.strip().lower() in _SEEDREAM55_BACKGROUNDS:
            body["background"] = bg.strip().lower()

    sid = params["safety_identifier"] if "safety_identifier" in params else None
    if isinstance(sid, str) and sid:
        body["safety_identifier"] = sid

    # 5.5 只支持 standard 提示词优化;validate 已拦非 standard,这里防御性兜底
    opt_mode = params["optimize_mode"] if "optimize_mode" in params else None
    if opt_mode == "standard":
        body["optimize_prompt_options"] = {"mode": "standard"}
    elif opt_mode is not None:
        raise RuntimeError(f"Seedream 5.5 仅支持 standard 提示词优化模式,收到 {opt_mode!r}")

    if request.seed is not None:
        body["seed"] = int(request.seed)

    from ...core.telemetry.wire_capture import set_wire_from_http_body

    set_wire_from_http_body(body)
    result = await api.generate(body, decode_all=True)

    usage: dict[str, object] = {
        "model": result["model"],
        "size": result["size"],
        "generated_images": result["generated_images"],
        "input_images": result["input_images"] or len(request.images),
        "output_format": result["output_format"] or output_format,
        "vendor": "ark",
    }

    if not layer_mode:
        data, mime = await asyncio.to_thread(encode_pil_image, result["image"], output_format)
        return NodeOutput(
            status="ok",
            output_type="image",
            data=data,
            mime_type=mime,
            outputs={"image": data},
            usage=usage,
            raw=result["raw"],
        )

    # 图层拆分:底图(z_index=0)作主产物,其余图层按 z_index 进 outputs
    outputs: dict[str, bytes] = {}
    layers_meta: list[dict[str, object]] = []
    base_data: bytes | None = None
    base_mime = "image/png"
    for i, (pil, item) in enumerate(zip(result["images"], result["items"], strict=True)):
        z_raw = item["z_index"] if isinstance(item, dict) and "z_index" in item else None
        z = _opt_int(z_raw)
        if z is None:
            z = i
        layers_meta.append(_layer_manifest_entry(item))
        img_bytes, img_mime = await asyncio.to_thread(encode_pil_image, pil, output_format if z == 0 else "png")
        if z == 0:
            base_data, base_mime = img_bytes, img_mime
            outputs["image"] = img_bytes
        else:
            outputs[f"layer_{z:02d}"] = img_bytes
    if base_data is None:
        raise RuntimeError("Seedream 5.5 图层拆分响应缺少 z_index=0 的底图")
    usage["layers"] = layers_meta

    return NodeOutput(
        status="ok",
        output_type="image",
        data=base_data,
        mime_type=base_mime,
        outputs=outputs,
        usage=usage,
        raw=result["raw"],
    )


__all__ = [
    "resolve_seedream_size",
    "seedream55_mapper",
    "seedream_mapper",
]
