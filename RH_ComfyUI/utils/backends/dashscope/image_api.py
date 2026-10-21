"""千问图像的 DashScope 同步客户端。

POST {base}/services/aigc/multimodal-generation/generation。
不发送 X-DashScope-Async,也不走 text2image 任务接口。
"""

from __future__ import annotations

import json
from typing import Protocol
from dataclasses import dataclass

import httpx

from gsuid_core.logger import logger

from .config import dashscope_credentials
from ..http_retry import RetryingAsyncClient, download_with_network_retry
from ...image_process import image_mime_from_bytes
from ....core.telemetry.wire_capture import set_vendor_raw, set_wire_from_http_body

_GENERATION_PATH = "/services/aigc/multimodal-generation/generation"


class ImagePost(Protocol):
    async def __call__(self, url: str, headers: dict[str, str], body: object) -> httpx.Response: ...


@dataclass(frozen=True)
class QwenImageResult:
    images: tuple[bytes, ...]
    mime: str
    usage: dict[str, object]
    request_id: str


def generation_url(base_url: str) -> str:
    return base_url.strip().rstrip("/") + _GENERATION_PATH


def _loads(raw: bytes) -> object:
    if not raw:
        return None
    # 失败响应有时不是 JSON。
    try:
        value: object = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value


def _text_field(payload: object, key: str) -> str:
    if not isinstance(payload, dict) or key not in payload:
        return ""
    value = payload[key]
    if isinstance(value, str):
        return value.strip()
    return ""


def qwen_image_error_text(status: int, payload: object) -> str:
    code = ""
    message = ""
    request_id = ""
    if isinstance(payload, dict):
        code = _text_field(payload, "code")
        message = _text_field(payload, "message")
        request_id = _text_field(payload, "request_id")
    parts: list[str] = []
    if code:
        parts.append(code)
    if message:
        parts.append(message)
    if request_id:
        parts.append(request_id)
    if not parts:
        parts.append(f"HTTP {status}")
    return "DashScope 图像生成失败: " + " / ".join(parts)


def image_urls_from_payload(payload: object) -> list[str]:
    if not isinstance(payload, dict) or "output" not in payload:
        return []
    output = payload["output"]
    if not isinstance(output, dict) or "choices" not in output:
        return []
    choices = output["choices"]
    if not isinstance(choices, list):
        return []
    found: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict) or "message" not in choice:
            continue
        message = choice["message"]
        if not isinstance(message, dict) or "content" not in message:
            continue
        content = message["content"]
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or "image" not in part:
                continue
            image = part["image"]
            if isinstance(image, str) and image.strip():
                found.append(image.strip())
    return found


def _usage_fields(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or "usage" not in payload:
        return {}
    raw = payload["usage"]
    if not isinstance(raw, dict):
        return {}
    out: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            out[key] = value
        elif isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    return out


def _request_id(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return _text_field(payload, "request_id")


class DashScopeImageAPI:
    """凭证每次调用再读。空 Key 不会拼成 Bearer。"""

    def __init__(self, post: ImagePost | None = None) -> None:
        self._post = post

    def is_configured(self) -> bool:
        creds = dashscope_credentials()
        return bool(creds.enabled and creds.api_key.strip() and creds.base_url)

    async def generate(self, body: object, *, prompt: str) -> QwenImageResult:
        creds = dashscope_credentials()
        key = creds.api_key.strip()
        if not creds.enabled:
            raise RuntimeError("DashScope 未启用:请在 Web 控制台打开 HappyHorse_Enable_dashscope")
        if not key:
            raise RuntimeError("未配置 DashScope API Key,请在 Web 控制台填写 HappyHorse_apikey_dashscope")
        base = (creds.base_url or "").strip()
        if not base:
            raise RuntimeError("未配置 DashScope Base URL")
        url = generation_url(base)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        model = ""
        if isinstance(body, dict) and "model" in body:
            model_value = body["model"]
            if isinstance(model_value, str):
                model = model_value
        # messages 里的 text 提取器走不到,prompt 要显式传入。
        set_wire_from_http_body(body, prompt=prompt)
        if self._post is not None:
            response = await self._post(url, headers, body)
        else:
            timeout = httpx.Timeout(600.0, connect=30.0)
            async with RetryingAsyncClient(timeout=timeout, trust_env=True) as client:
                response = await client.post(url, headers=headers, json=body)
        logger.info(f"[qwen-image] {model or '-'} HTTP {response.status_code}")
        payload = _loads(response.content)
        if isinstance(payload, dict):
            set_vendor_raw(payload)
        failed = response.status_code != 200
        if isinstance(payload, dict) and _text_field(payload, "code"):
            failed = True
        if failed:
            raise RuntimeError(qwen_image_error_text(response.status_code, payload))
        urls = image_urls_from_payload(payload)
        if not urls:
            request_id = _request_id(payload)
            extra = f" / {request_id}" if request_id else ""
            raise RuntimeError(f"DashScope 图像生成未返回图片{extra}")
        images: list[bytes] = []
        for image_url in urls:
            images.append(await download_with_network_retry(image_url, timeout=120.0, label="qwen-image"))
        usage = _usage_fields(payload)
        request_id = _request_id(payload)
        if request_id:
            usage["request_id"] = request_id
        return QwenImageResult(
            images=tuple(images),
            mime=image_mime_from_bytes(images[0]),
            usage=usage,
            request_id=request_id,
        )


__all__ = [
    "DashScopeImageAPI",
    "ImagePost",
    "QwenImageResult",
    "generation_url",
    "image_urls_from_payload",
    "qwen_image_error_text",
]
