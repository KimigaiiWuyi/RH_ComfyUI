"""GPT-Image2 API 客户端 — 封装 OpenAI 兼容 API 的图片生成接口"""

from __future__ import annotations

import io
import re
import base64
import asyncio
from typing import Any, Dict, List, Union, Literal, Optional

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG


class GPTImage2API:
    """GPT-Image2 / OpenAI 兼容 API 客户端"""

    def __init__(self) -> None:
        self._api_key: Optional[str] = None
        self._base_url: Optional[str] = None
        self.chat_url = ""
        self.images_url = ""

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = SERVICE_CONFIG.get_config("OpenAI_Image_apikey").data or ""
        return self._api_key or ""

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = (
                SERVICE_CONFIG.get_config("OpenAI_Image_BaseURL").data or "https://api.openai.com/v1"
            )
        return self._base_url or "https://api.openai.com/v1"

    def update_urls(self) -> None:
        base = self.base_url.rstrip("/")
        # base_url 通常已包含 /v1, 避免拼出 /v1/v1/...
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self.chat_url = f"{base}/v1/chat/completions"
        self.images_url = f"{base}/v1/images/generations"

    def refresh_config(self) -> None:
        self._api_key = None
        self._base_url = None
        # 强制清空缓存,下次访问时重新读取 OpenAI_Image_* 配置
        self.update_urls()

    async def _base_request(
        self,
        method: Literal["POST", "GET"],
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Union[Dict[str, Any], int]:
        logger.info(f"[GPT-Image2] 请求: {method} {url}")

        params: dict[str, Any] = {}
        if json:
            params["json"] = json
        if data:
            params["data"] = data

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers, **params) as resp:
                    logger.info(f"[GPT-Image2] 响应状态: {resp.status}")

                    if resp.status != 200:
                        try:
                            err_body = await resp.text()
                            logger.warning(f"[GPT-Image2] 错误响应体: {err_body[:500]}")
                        except Exception:
                            pass
                        return resp.status

                    resp_data = await resp.json()
                    logger.debug(f"[GPT-Image2] 响应数据: {resp_data}")
                    return resp_data

        except Exception as e:
            logger.warning(f"[GPT-Image2] 请求失败: {e}")
            return 500

    async def _request(
        self,
        method: Literal["POST", "GET"],
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Union[Dict[str, Any], int]:
        fail_count = 0
        self.update_urls()

        while fail_count < max_retries:
            try:
                if not headers:
                    headers = {}

                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                else:
                    logger.warning("[GPT-Image2] 未配置 API Key，可能导致请求失败！")

                resp = await self._base_request(method, url, headers, json, data)

                if isinstance(resp, int):
                    if resp == 421:
                        logger.info("[GPT-Image2] 请求过于频繁(421)，等待 180 秒后重试...")
                        await asyncio.sleep(180)
                        continue

                    fail_count += 1
                    logger.warning(f"[GPT-Image2] 请求返回错误状态码: {resp}, 重试 ({fail_count}/{max_retries})")
                    continue

                return resp

            except Exception as e:
                logger.warning(f"[GPT-Image2] 请求异常: {e}, 重试 ({fail_count + 1}/{max_retries})")
                fail_count += 1
                await asyncio.sleep(1)
                continue

        logger.error("[GPT-Image2] 请求重试耗尽，最终失败")
        return 500

    async def _download_image_from_url(self, url: str) -> Union[Image.Image, int]:
        logger.info(f"[GPT-Image2] 下载图片: {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"[GPT-Image2] 下载图片失败，状态码: {resp.status}")
                        return 500
                    image_data = await resp.read()
                    return Image.open(io.BytesIO(image_data))
        except Exception as e:
            logger.warning(f"[GPT-Image2] 下载图片失败: {e}")
            return 500

    @staticmethod
    def _decode_base64_image(base64_data: str) -> Union[Image.Image, int]:
        try:
            if base64_data.startswith("data:"):
                pattern = r"data:image/([a-zA-Z+]+);base64,(.+)"
                match = re.match(pattern, base64_data)
                if match:
                    base64_data = match.group(2)

            image_bytes = base64.b64decode(base64_data)
            return Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            logger.warning(f"[GPT-Image2] 解码 base64 图片失败: {e}")
            return 500

    async def _parse_image_from_content(self, content: str) -> Union[Image.Image, int]:
        content = content.strip()

        if content.startswith("data:") or (
            len(content) > 100 and "/" not in content and not content.startswith(("http://", "https://"))
        ):
            return self._decode_base64_image(content)

        if content.startswith(("http://", "https://")):
            return await self._download_image_from_url(content)

        logger.warning(f"[GPT-Image2] 无法识别的图片内容格式: {content[:50]}...")
        return 500

    async def draw_image_by_model(
        self,
        model: str,
        prompt: str,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        n: Optional[int] = None,
        stream: bool = False,
        max_tokens: Optional[int] = None,
    ) -> Union[Image.Image, int]:
        """通过 Chat Completions API 生图 (部分兼容 OpenAI 格式的第三方服务直接在 chat 中返回图片链接/base64)"""
        logger.info(f"[GPT-Image2] Chat生图: model={model}, prompt={prompt}")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        request_body: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }

        if temperature is not None:
            request_body["temperature"] = temperature
        if top_p is not None:
            request_body["top_p"] = top_p
        if n is not None:
            request_body["n"] = n
        if max_tokens is not None:
            request_body["max_tokens"] = max_tokens

        resp = await self._request("POST", self.chat_url, headers=headers, json=request_body)

        if isinstance(resp, int):
            return resp

        try:
            if "choices" not in resp or not resp["choices"]:
                return 500

            choice = resp["choices"][0]
            content = choice["message"]["content"]
            return await self._parse_image_from_content(content)

        except Exception as e:
            logger.error(f"[GPT-Image2] 解析 Chat Completions 失败: {e}")
            return 500

    # ratio + image_size → OpenAI images API 的 size 参数映射(二维)
    # 有效尺寸:1024x1024 / 1536x1024 / 1024x1536 / 2048x2048 / 2048x1152 /
    #          3840x2160 / 2160x3840 / auto
    # 约束:最大边 ≤3840 / 双边 16 整除 / 长宽比 ≤3:1 / 像素 ∈ [655360, 8294400]
    _RATIO_SIZE_MAP: Dict[str, Dict[str, str]] = {
        "1:1":   {"1K": "1024x1024", "2K": "2048x2048", "4K": "3840x2160"},
        "16:9":  {"1K": "1024x1024", "2K": "2048x1152", "4K": "3840x2160"},
        "9:16":  {"1K": "1024x1024", "2K": "2048x2048", "4K": "2160x3840"},
        "4:3":   {"1K": "1024x1024", "2K": "2048x2048", "4K": "3840x2160"},
        "3:4":   {"1K": "1024x1024", "2K": "2048x2048", "4K": "2160x3840"},
        "3:2":   {"1K": "1536x1024", "2K": "2048x1152", "4K": "3840x2160"},
        "2:3":   {"1K": "1024x1536", "2K": "2048x2048", "4K": "2160x3840"},
        "21:9":  {"1K": "1024x1024", "2K": "2048x1152", "4K": "3840x2160"},
    }

    @staticmethod
    def resolve_size(ratio: Optional[str], image_size: Optional[str]) -> str:
        """ratio + image_size → 像素尺寸字符串。

        未匹配的组合回落到 2048x2048(2K 正方形,满足全部约束);
        ratio 为 "auto" 或空 → 返回 "auto"。
        """
        if not ratio or ratio == "auto":
            return "auto"
        tier = image_size if image_size in ("1K", "2K", "4K") else "2K"
        tier_map = GPTImage2API._RATIO_SIZE_MAP.get(ratio)
        if tier_map is None:
            return "auto"
        return tier_map.get(tier, "2048x2048")

    async def draw_image(
        self,
        model: str,
        prompt: str,
        aspect_ratio: Optional[str] = "1:1",
        image_size: Optional[str] = "2K",
        quality: Optional[str] = "medium",
        image_list: Optional[List[bytes]] = None,
    ) -> Union[Image.Image, int]:
        """通过 DALL-E 格式 API 生图 (/v1/images/generations)

        aspect_ratio + image_size → size 像素值;quality 直接透传给上游。
        """
        size = self.resolve_size(aspect_ratio, image_size)
        logger.info(f"[GPT-Image2] Dall-e生图: model={model}, prompt={prompt}, size={size}, quality={quality}")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        request_body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
        }

        request_body["size"] = size
        if quality and quality in ("low", "medium", "high"):
            request_body["quality"] = quality
        if image_list is not None:
            request_body["image"] = [base64.b64encode(img_bytes).decode() for img_bytes in image_list]

        resp = await self._request("POST", self.images_url, headers=headers, json=request_body)

        if isinstance(resp, int):
            return resp

        try:
            if "data" not in resp or not resp["data"]:
                return 500

            data_item = resp["data"][0]
            image_content = data_item.get("url") or (
                f"data:image/png;base64,{data_item['b64_json']}" if "b64_json" in data_item else None
            )

            if not image_content:
                return 500

            return await self._parse_image_from_content(image_content)

        except Exception as e:
            logger.error(f"[GPT-Image2] 解析 Dall-e API 失败: {e}")
            return 500


# 全局单例
gpt_image2_api = GPTImage2API()
