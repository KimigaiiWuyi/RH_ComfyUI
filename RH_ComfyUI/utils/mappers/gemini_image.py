"""Gemini 生图映射函数 — 自适应文生图 / 图生图(编辑)"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..core.types import NodeOutput
from ..core.request import GenerationRequest

if TYPE_CHECKING:
    from ..backends.gemini_image.api import GeminiImageAPI

# generate_content image_config.aspect_ratio 白名单(上游 400 原文)。
# 产品面仍可暴露 8:5 等,发请求前必须折到这里。
GEMINI_ASPECT_RATIOS: tuple[str, ...] = (
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
)


def _default_image_size(model: str) -> Optional[str]:
    """尺寸档默认值:一代(gemini-2.5-flash-image)不支持 image_config.image_size,
    必须整个字段不发;3.x 系(banana2 flash / banana_pro)保持既有默认 2K。"""
    return None if "2.5" in model else "2K"


def _ratio_value(label: str) -> float | None:
    parts = label.split(":")
    if len(parts) != 2:
        return None
    try:
        w = float(parts[0])
        h = float(parts[1])
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return w / h


def snap_gemini_aspect_ratio(ratio: str) -> str:
    """把任意 ratio 折到 Gemini 白名单;已合法则原样返回。"""
    text = (ratio or "").strip()
    if not text or text.lower() == "auto":
        return "1:1"
    if text in GEMINI_ASPECT_RATIOS:
        return text
    target = _ratio_value(text)
    if target is None:
        return "1:1"
    best = "1:1"
    best_dist = abs((_ratio_value(best) or 1.0) - target)
    for cand in GEMINI_ASPECT_RATIOS:
        val = _ratio_value(cand)
        if val is None:
            continue
        dist = abs(val - target)
        if dist < best_dist:
            best = cand
            best_dist = dist
    return best


def _gemini_ratio(request: GenerationRequest) -> str:
    """Gemini 只认白名单宽高比;8:5 等产品面比例就近折过去。"""
    return snap_gemini_aspect_ratio(request.ratio or "")


async def gemini_flash_image_mapper(
    request: GenerationRequest,
    api: "GeminiImageAPI",
) -> NodeOutput:
    """有图走图生图(编辑),无图走文生图。Gemini 只吃 aspect_ratio + image_size。"""
    model = request.params.get("model") or "gemini-3.1-flash-image-preview"
    raw_ratio = (request.ratio or "").strip() or "auto"
    ratio = _gemini_ratio(request)
    if ratio != raw_ratio and raw_ratio.lower() != "auto":
        from gsuid_core.logger import logger

        logger.info(f"[Gemini-Image] aspect_ratio {raw_ratio} 不在上游白名单,改发 {ratio}")
    raw_size = request.params.get("image_size")
    image_size = str(raw_size) if raw_size else _default_image_size(model)

    data = await api.generate(
        model=model,
        prompt=request.prompt,
        images=request.images or None,
        aspect_ratio=ratio,
        image_size=image_size,
    )
    return NodeOutput(
        status="ok",
        output_type="image",
        data=data,
        mime_type="image/png",
        outputs={"image": data},
    )
