"""OpenAI 兼容生图客户端(通用) — 供多家 provider 复用。

每次请求由 channel 传入 base_url + api_key(实时解析,支持热更新)。端点固定为
``{base_url}/images/generations``, body ``{model, prompt, n[, size]}``, 解析
``data[0].url | b64_json`` 为 PNG 字节。百度千帆 / OpenAI 官方 / 各类兼容网关通用。
"""

from __future__ import annotations

import io
import base64
from typing import Any, Dict, List, Optional

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

# aspect_ratio -> OpenAI images size(与 gpt_image2 对齐)
_RATIO_TO_SIZE: Dict[str, str] = {
    "1:1": "1024x1024",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
    "4:3": "1024x1024",
    "3:4": "1024x1024",
    "21:9": "1792x1024",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
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
    return min(_RATIO_TO_SIZE, key=lambda k: abs(_ratio_value(k) - actual))


def size_for(ratio: Optional[str], width: int, height: int) -> str:
    key = ratio or ratio_from_wh(width, height)
    return _RATIO_TO_SIZE[key] if key in _RATIO_TO_SIZE else "1024x1024"


def _ratio_value(ratio: str) -> float:
    w, _, h = ratio.partition(":")
    return int(w) / int(h) if h and int(h) else 1.0


def _to_png_bytes(raw: bytes) -> bytes:
    """统一转 PNG 字节(上游可能返回 jpeg/webp),保证 mime 一致。"""
    with Image.open(io.BytesIO(raw)) as img:
        buf = io.BytesIO()
        img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(buf, format="PNG")
        return buf.getvalue()


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


async def generate_image(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    n: int = 1,
    size: Optional[str] = None,
    image_list: Optional[List[bytes]] = None,
) -> bytes:
    """POST {base_url}/images/generations, 返回第一张图的 PNG 字节。失败抛 OpenAIImageError。"""
    url = f"{base_url.rstrip('/')}/images/generations"
    headers: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:  # 空 key 不拼 Bearer, 避免 httpx/aiohttp 非法头
        headers["Authorization"] = f"Bearer {api_key}"

    body: Dict[str, Any] = {"model": model, "prompt": prompt, "n": n}
    if size:
        body["size"] = size
    if image_list:
        body["image"] = [base64.b64encode(b).decode() for b in image_list]

    logger.info(f"[OpenAIImage] 请求 {url} model={model} n={n} size={size or '-'} 参考图={len(image_list or [])}")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
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
        return _to_png_bytes(base64.b64decode(b64))
    result_url = first.get("url")
    if isinstance(result_url, str) and result_url:
        return _to_png_bytes(await _download(result_url))
    raise OpenAIImageError(f"{model} data[0] 无 url/b64_json", user_message="生图服务未返回可用图片。")


__all__ = ["OpenAIImageError", "generate_image", "size_for", "ratio_from_wh"]
