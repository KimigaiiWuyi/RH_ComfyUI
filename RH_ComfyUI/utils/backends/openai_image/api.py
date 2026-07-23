"""OpenAI 兼容生图客户端(通用) — 供多家 provider 复用。

每次请求由 channel 传入 base_url + api_key(实时解析,支持热更新)。
统一走 ``POST {base_url}/images/edits``(标准 OpenAI multipart 协议):
- ``image`` 为文件字段(纯文生图时不传);多图时按官方 SDK 惯例用 ``image[]``
- ``quality`` 必传(low/medium/high),始终透传给上游

响应解析 ``data[0].url | b64_json`` 为 PNG 字节。
百度千帆 / OpenAI 官方 / 各类兼容网关通用。
"""

from __future__ import annotations

import io
import base64
import asyncio
from typing import Any, Dict, List, Optional

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

# ratio + image_size → OpenAI images size 映射(二维,与 gpt_image2_billing 对齐)
# 有效尺寸必须满足上游约束:最大边 ≤3840 / 双边 16 整除 / 长宽比精确且 ≤3:1 /
#                       像素 ∈ [655360, 8294400]
# tier 语义:短边 ≈ 1K/2K/4K。
# 注:本表 2026-07 重建,纠正早期"占位值"错误(2:3 + 2K 不再是正方形)。
_RATIO_SIZE_MAP: Dict[str, Dict[str, str]] = {
    # Landscape (width > height)
    "1:1":   {"1K": "1024x1024", "2K": "2048x2048", "4K": "2880x2880"},
    "16:9":  {"1K": "1792x1008", "2K": "2048x1152", "4K": "3840x2160"},
    "4:3":   {"1K": "1280x960",  "2K": "2048x1536", "4K": "3264x2448"},
    "3:2":   {"1K": "1536x1024", "2K": "3072x2048", "4K": "3456x2304"},
    "21:9":  {"1K": "2464x1056", "2K": "2688x1152", "4K": "3808x1632"},
    # Portrait (width < height)
    "9:16":  {"1K": "1008x1792", "2K": "1152x2048", "4K": "2160x3840"},
    "3:4":   {"1K": "960x1280",  "2K": "1536x2048", "4K": "2448x3264"},
    "2:3":   {"1K": "1024x1536", "2K": "2048x3072", "4K": "2304x3456"},
}

# 扁平旧表保留作 fallback 用(取 2K 档)
_RATIO_TO_SIZE: Dict[str, str] = {
    ratio: tiers["2K"] for ratio, tiers in _RATIO_SIZE_MAP.items()
}


class OpenAIImageError(RuntimeError):
    """上游生图失败(带可选 http 状态与面向用户文案)。"""

    def __init__(
        self,
        message: str,
        *,
        http_status: Optional[int] = None,
        user_message: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.user_message = user_message or message


def ratio_from_wh(width: int, height: int) -> str:
    """按宽高取最接近的枚举宽高比(供无 ratio 端口的模型如 qwen 用)。"""
    actual = width / height if height else 1.0
    return min(_RATIO_SIZE_MAP, key=lambda k: abs(_ratio_value(k) - actual))


def size_for(
    ratio: Optional[str],
    width: int,
    height: int,
    *,
    image_size: Optional[str] = None,
) -> str:
    """ratio(+image_size) → 像素尺寸字符串。

    签名保持向后兼容(第三关键字参数 image_size 可选),旧二参数调用回落 2K 档。
    - ratio 为 "auto" → "auto"
    - ratio 为 None → 按 width/height 最接近枚举兜底(旧二参数行为)
    - 未匹配组合回落 2048x2048
    """
    # 显式 auto → "auto"
    if ratio == "auto":
        return "auto"
    # ratio 缺失 → 按像素宽高取最接近枚举
    key = ratio or ratio_from_wh(width, height)
    if image_size:
        tier = image_size if image_size in ("1K", "2K", "4K") else "2K"
        tier_map = _RATIO_SIZE_MAP.get(key)
        if tier_map is None:
            return "auto"
        return tier_map.get(tier, "2048x2048")
    # 旧二参数形态:仅 ratio(或按 wh 兜底),取 2K 档
    return _RATIO_TO_SIZE.get(key, "2048x2048")


def _ratio_value(ratio: str) -> float:
    w, _, h = ratio.partition(":")
    return int(w) / int(h) if h and int(h) else 1.0


def _to_png_bytes(raw: bytes) -> bytes:
    """统一转 PNG 字节(上游可能返回 jpeg/webp),保证 mime 一致。

    同步 CPU 重活:大图勿在事件循环直接调用,见 ``_to_png_bytes_async``。
    """
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            with Image.open(io.BytesIO(raw)) as img:
                if img.mode not in ("P", "LA"):
                    return raw
        except Exception:  # noqa: BLE001
            pass
    with Image.open(io.BytesIO(raw)) as img:
        buf = io.BytesIO()
        img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(buf, format="PNG")
        return buf.getvalue()


async def _to_png_bytes_async(raw: bytes) -> bytes:
    return await asyncio.to_thread(_to_png_bytes, raw)


async def _download(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise OpenAIImageError(
                    f"下载结果图失败 HTTP {resp.status}: {url}",
                    http_status=resp.status,
                    user_message="下载生成结果失败,请稍后重试。",
                )
            return await resp.read()


def _edits_fields(
    *,
    model: str,
    prompt: str,
    n: int,
    size: Optional[str],
    quality: str,
    image_list: List[bytes],
) -> List[tuple]:
    """/images/edits 的 multipart 字段表(纯函数,供单测断言协议形状)。

    字段名遵循官方 SDK 惯例:
    - 单图用 ``image``,多图每张一个 ``image[]`` 部件;
    - ``quality`` 必传,始终透传上游。
    """
    fields: List[tuple] = [
        ("model", model),
        ("prompt", prompt),
        ("n", str(n)),
        ("quality", quality),
    ]
    if size:
        fields.append(("size", size))
    image_field = "image" if len(image_list) == 1 else "image[]"
    fields.extend((image_field, raw) for raw in image_list)
    return fields


async def generate_image(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    quality: str,
    n: int = 1,
    size: Optional[str] = None,
    image_list: Optional[List[bytes]] = None,
) -> bytes:
    """生成/编辑一张图,返回 PNG 字节。失败抛 OpenAIImageError。

    统一走 /images/edits(multipart):纯文生图时不传 image 字段,带参考图时
    走 image / image[] 文件字段。quality 必传,始终透传给上游。
    """
    headers: Dict[str, str] = {"Accept": "application/json"}
    if api_key:  # 空 key 不拼 Bearer, 避免 httpx/aiohttp 非法头
        headers["Authorization"] = f"Bearer {api_key}"

    root = base_url.rstrip("/")
    url = f"{root}/images/edits"
    form = aiohttp.FormData()
    fields = _edits_fields(
        model=model, prompt=prompt, n=n, size=size, quality=quality, image_list=image_list or []
    )
    for i, (name, value) in enumerate(fields):
        if isinstance(value, bytes):
            form.add_field(name, value, filename=f"image_{i}.png", content_type="image/png")
        else:
            form.add_field(name, value)
    request_kwargs: Dict[str, Any] = {"data": form}

    logger.info(f"[OpenAIImage] 请求 {url} model={model} n={n} size={size or '-'} quality={quality} 参考图={len(image_list or [])}")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, **request_kwargs) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.warning(f"[OpenAIImage] HTTP {resp.status}: {text[:300]}")
                raise OpenAIImageError(
                    f"{model} 生图 HTTP {resp.status}: {text[:300]}",
                    http_status=resp.status,
                    user_message="生图服务返回错误,请稍后重试。",
                )
            data = await resp.json()

    return await _extract_image(data, model)


async def _extract_image(data: Any, model: str) -> bytes:
    if not isinstance(data, dict):
        raise OpenAIImageError(f"{model} 返回非 JSON 对象: {type(data).__name__}")
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise OpenAIImageError(f"{model} 响应缺少 data: {str(data)[:200]}", user_message="生图服务未返回图片。")
    first = items[0]
    if not isinstance(first, dict):
        raise OpenAIImageError(f"{model} data[0] 非对象")

    b64 = first.get("b64_json")
    if isinstance(b64, str) and b64:
        return await _to_png_bytes_async(base64.b64decode(b64))
    result_url = first.get("url")
    if isinstance(result_url, str) and result_url:
        return await _to_png_bytes_async(await _download(result_url))
    raise OpenAIImageError(f"{model} data[0] 无 url/b64_json", user_message="生图服务未返回可用图片。")


__all__ = ["OpenAIImageError", "generate_image", "size_for", "ratio_from_wh"]
