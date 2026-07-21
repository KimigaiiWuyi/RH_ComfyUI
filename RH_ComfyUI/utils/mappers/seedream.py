"""Seedream 5.0 (Lite / Pro) 生图 mapper — 同步 /v1/images/generations

负责把 GenerationRequest 翻译成 Seedream 同步接口的请求体:
- prompt + model 由 NodeDef.backend_model 注入
- 参考图(`request.images` bytes 列表)经 R2 上传后组成 image 字段(string / string[])
- size 由 size_mode 档位 + ratio 宽高比自动计算为像素值(文档「方式 2」),
  不允许混用档位字符串与像素字符串。映射表来自火山方舟 Seedream 5.0 官方文档。
"""

from __future__ import annotations

import io
import base64
from typing import TYPE_CHECKING

from ..core.types import NodeOutput

if TYPE_CHECKING:
    from ..backends.seedream.api import SeedreamAPI


# ── size 映射表(来自 Seedream 5.0 官方文档「方式 2」) ───────────────────
#
# Seedream 5.0 Pro 仅支持 1K/2K 两档;Lite 支持 2K/3K/4K。
# 每个档位下各宽高比对应固定的宽高像素值(总像素需在约束范围内)。


# Pro: 1K/2K
_SEEDREAM_PRO_SIZE_MAP: dict[str, dict[str, str]] = {
    "1K": {
        "1:1": "1024x1024", "4:3": "1152x864", "3:4": "864x1152",
        "16:9": "1424x800", "9:16": "800x1424", "3:2": "1248x832",
        "2:3": "832x1248", "21:9": "1568x672",
    },
    "2K": {
        "1:1": "2048x2048", "4:3": "2368x1776", "3:4": "1776x2368",
        "16:9": "2816x1584", "9:16": "1584x2816", "3:2": "2496x1664",
        "2:3": "1664x2496", "21:9": "3136x1344",
    },
}

# Lite: 2K/3K/4K
_SEEDREAM_LITE_SIZE_MAP: dict[str, dict[str, str]] = {
    "2K": {
        "1:1": "2048x2048", "4:3": "2304x1728", "3:4": "1728x2304",
        "16:9": "2848x1600", "9:16": "1600x2848", "3:2": "2496x1664",
        "2:3": "1664x2496", "21:9": "3136x1344",
    },
    "3K": {
        "1:1": "3072x3072", "4:3": "3456x2592", "3:4": "2592x3456",
        "16:9": "4096x2304", "9:16": "2304x4096", "3:2": "3744x2496",
        "2:3": "2496x3744", "21:9": "4704x2016",
    },
    "4K": {
        "1:1": "4096x4096", "4:3": "4704x3520", "3:4": "3520x4704",
        "16:9": "5504x3040", "9:16": "3040x5504", "3:2": "4992x3328",
        "2:3": "3328x4992", "21:9": "6240x2656",
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
            body["image"] = _encode_images_to_data_urls(list(request.images))

    # Pro 不接受 Lite-only 字段(防御性拦截)
    if _is_pro_model(model):
        for pro_blocked in ("sequential_image_generation", "sequential_image_generation_options", "tools"):
            if pro_blocked in request.params:
                raise RuntimeError(
                    f"Seedream 5.0 Pro 不支持字段 {pro_blocked},请改用 Seedream 5.0 Lite"
                )
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

    result = await api.generate(body)

    pil_image = result["image"]
    buf = io.BytesIO()
    # 输出 mime 与 output_format 对齐
    save_format = "PNG" if (output_format or "").lower() == "png" else "JPEG"
    pil_image.save(buf, format=save_format)
    data = buf.getvalue()
    mime = "image/png" if save_format == "PNG" else "image/jpeg"

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


__all__ = [
    "resolve_seedream_size",
    "seedream_mapper",
]
