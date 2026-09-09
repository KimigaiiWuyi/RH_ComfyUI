"""OpenAI 兼容生图客户端(通用) — 供多家 provider 复用。

每次请求由 channel 传入 base_url + api_key(实时解析,支持热更新)。
统一走 ``POST {base_url}/images/edits``(标准 OpenAI multipart 协议):
- ``image`` 为文件字段(纯文生图时不传);多图时按官方 SDK 惯例用 ``image[]``
- ``quality`` 必传(low/medium/high/xhigh/max),始终透传给上游

响应解析 ``data[0].url | b64_json`` 为请求的编码格式字节(缺省仍转 PNG)。
百度千帆 / OpenAI 官方 / 各类兼容网关通用。
"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

import httpx
import aiohttp

from gsuid_core.logger import logger

from ..http_retry import call_with_network_retry, download_with_network_retry
from ...image_process import encode_image_bytes_async

# 像素真源:gpt_image2_billing._RATIO_SIZE_MAP(计费 / 生图 / schema 共用)
from ...mappers.gpt_image2_billing import (
    _RATIO_SIZE_MAP,
    resolve_size_string,
)

# 扁平旧表保留作 fallback 用(取 2K 档)
_RATIO_TO_SIZE: Dict[str, str] = {ratio: tiers["2K"] for ratio, tiers in _RATIO_SIZE_MAP.items()}


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
        # 与计费同源(含 1:2 / 2:1 等)
        return resolve_size_string(key, image_size)
    # 旧二参数形态:仅 ratio(或按 wh 兜底),取 2K 档
    return _RATIO_TO_SIZE.get(key, "2048x2048")


def _ratio_value(ratio: str) -> float:
    w, _, h = ratio.partition(":")
    return int(w) / int(h) if h and int(h) else 1.0


def _to_png_bytes(raw: bytes) -> bytes:
    """统一转 PNG 字节(上游可能返回 jpeg/webp),保证 mime 一致。

    同步 CPU 重活:大图勿在事件循环直接调用,见 ``_to_png_bytes_async``。
    """
    from ...image_process import encode_image_bytes

    return encode_image_bytes(raw, "png")


async def _to_png_bytes_async(raw: bytes) -> bytes:
    return await asyncio.to_thread(_to_png_bytes, raw)


async def _download(url: str) -> bytes:
    try:
        return await download_with_network_retry(url, timeout=120.0, label="OpenAIImage")
    except httpx.HTTPStatusError as exc:
        raise OpenAIImageError(
            f"下载结果图失败 HTTP {exc.response.status_code}: {url}",
            http_status=exc.response.status_code,
            user_message="下载生成结果失败,请稍后重试。",
        ) from exc


def _edits_fields(
    *,
    model: str,
    prompt: str,
    n: int,
    size: Optional[str],
    quality: str,
    image_list: List[bytes],
    background: Optional[str] = None,
    output_format: Optional[str] = None,
) -> List[tuple]:
    """/images/edits 的 multipart 字段表(纯函数,供单测断言协议形状)。

    字段名遵循官方 SDK 惯例:
    - 单图用 ``image``,多图每张一个 ``image[]`` 部件;
    - ``quality`` 必传,始终透传上游;
    - ``background`` / ``output_format`` 仅 gpt-image 系有,缺省不发以免其它模型拒收。
    """
    fields: List[tuple] = [
        ("model", model),
        ("prompt", prompt),
        ("n", str(n)),
        ("quality", quality),
    ]
    if size:
        fields.append(("size", size))
    if background:
        fields.append(("background", background))
    if output_format:
        fields.append(("output_format", output_format))
    image_field = "image" if len(image_list) == 1 else "image[]"
    fields.extend((image_field, raw) for raw in image_list)
    return fields


@dataclass
class OpenAIImageResult:
    data: bytes
    raw: dict[str, object] = field(default_factory=dict)


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
    background: Optional[str] = None,
    output_format: Optional[str] = None,
) -> bytes:
    """生成/编辑一张图。失败抛 OpenAIImageError。

    统一走 /images/edits(multipart):纯文生图时不传 image 字段,带参考图时
    走 image / image[] 文件字段。quality 必传。
    指定 output_format 时按该格式落盘;否则转 PNG(千帆等旧模型兼容)。
    """
    packed = await generate_image_result(
        base_url=base_url,
        api_key=api_key,
        model=model,
        prompt=prompt,
        quality=quality,
        n=n,
        size=size,
        image_list=image_list,
        background=background,
        output_format=output_format,
    )
    return packed.data


async def generate_image_result(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    quality: str,
    n: int = 1,
    size: Optional[str] = None,
    image_list: Optional[List[bytes]] = None,
    background: Optional[str] = None,
    output_format: Optional[str] = None,
) -> OpenAIImageResult:
    """同 generate_image,额外带回上游 JSON(含 usage)。"""
    headers: Dict[str, str] = {"Accept": "application/json"}
    if api_key:  # 空 key 不拼 Bearer, 避免 httpx/aiohttp 非法头
        headers["Authorization"] = f"Bearer {api_key}"

    root = base_url.rstrip("/")
    url = f"{root}/images/edits"
    form = aiohttp.FormData()
    fields = _edits_fields(
        model=model,
        prompt=prompt,
        n=n,
        size=size,
        quality=quality,
        image_list=image_list or [],
        background=background,
        output_format=output_format,
    )
    for i, (name, value) in enumerate(fields):
        if isinstance(value, bytes):
            form.add_field(name, value, filename=f"image_{i}.png", content_type="image/png")
        else:
            form.add_field(name, value)
    request_kwargs: Dict[str, Any] = {"data": form}

    n_refs = len(image_list or [])
    logger.info(
        f"[OpenAIImage] 请求 {url} model={model} n={n} size={size or '-'} "
        f"quality={quality} background={background or '-'} output_format={output_format or '-'} 参考图={n_refs}"
    )
    from ....core.telemetry.wire_capture import set_wire_audit

    set_wire_audit(
        prompt=prompt,
        request={
            "url": url,
            "model": model,
            "prompt": prompt,
            "quality": quality,
            "n": n,
            "size": size,
            "background": background,
            "output_format": output_format,
            "num_images": n_refs,
        },
    )

    async def _once() -> Any:
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
                return await resp.json()

    data = await call_with_network_retry(_once, label=f"POST {url}")
    image = await _extract_image(data, model, output_format=output_format)
    raw: dict[str, object] = data if isinstance(data, dict) else {}
    return OpenAIImageResult(data=image, raw=raw)


async def _extract_image(data: Any, model: str, *, output_format: Optional[str] = None) -> bytes:
    if not isinstance(data, dict):
        raise OpenAIImageError(f"{model} 返回非 JSON 对象: {type(data).__name__}")
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise OpenAIImageError(f"{model} 响应缺少 data: {str(data)[:200]}", user_message="生图服务未返回图片。")
    first = items[0]
    if not isinstance(first, dict):
        raise OpenAIImageError(f"{model} data[0] 非对象")

    raw: Optional[bytes] = None
    b64 = first.get("b64_json")
    if isinstance(b64, str) and b64:
        raw = base64.b64decode(b64)
    else:
        result_url = first.get("url")
        if isinstance(result_url, str) and result_url:
            raw = await _download(result_url)
    if raw is None:
        raise OpenAIImageError(f"{model} data[0] 无 url/b64_json", user_message="生图服务未返回可用图片。")
    if output_format:
        return await encode_image_bytes_async(raw, output_format)
    return await _to_png_bytes_async(raw)


__all__ = ["OpenAIImageError", "OpenAIImageResult", "generate_image", "generate_image_result", "size_for", "ratio_from_wh"]
