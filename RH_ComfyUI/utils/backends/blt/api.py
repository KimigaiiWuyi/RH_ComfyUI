"""BLT API 客户端 — 封装 OpenAI 兼容 API 的图片生成接口"""

from __future__ import annotations

import io
import re
import base64
import asyncio
from typing import Any, Dict, List, Union, Literal, Optional

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import RHCOMFYUI_CONFIG


class BLTAPI:
    """BLT / OpenAI 兼容 API 客户端"""

    def __init__(self) -> None:
        self.api_key: str = RHCOMFYUI_CONFIG.get_config("BLT_apikey").data
        self.base_url: str = RHCOMFYUI_CONFIG.get_config("BLT_API_URL").data
        self.chat_url = f"{self.base_url}/v1/chat/completions"
        self.images_url = f"{self.base_url}/v1/images/generations"

    async def _base_request(
        self,
        method: Literal["POST", "GET"],
        url: str,
        headers: Optional[Dict] = None,
        json: Optional[Dict] = None,
        data: Optional[Dict] = None,
    ) -> Union[Dict, int]:
        """基础 HTTP 请求"""
        logger.info(f"[BLT] 请求: {method} {url}")

        params: dict = {}
        if json:
            params["json"] = json
        if data:
            params["data"] = data

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers, **params) as resp:
                    logger.info(f"[BLT] 响应状态: {resp.status}")

                    if resp.status != 200:
                        return resp.status

                    resp_data = await resp.json()
                    logger.debug(f"[BLT] 响应数据: {resp_data}")
                    return resp_data

        except Exception as e:
            logger.warning(f"[BLT] 请求失败: {e}")
            return 500

    async def _request(
        self,
        method: Literal["POST", "GET"],
        url: str,
        headers: Optional[Dict] = None,
        json: Optional[Dict] = None,
        data: Optional[Dict] = None,
        max_retries: int = 3,
    ) -> Union[Dict, int]:
        """带重试机制的 HTTP 请求"""
        fail_count = 0

        while fail_count < max_retries:
            try:
                if not headers:
                    headers = {}

                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                else:
                    logger.warning("[BLT] 未配置API_KEY，将无法请求！")
                    return -1

                resp = await self._base_request(method, url, headers, json, data)

                if isinstance(resp, int):
                    if resp == 421:
                        logger.info("[BLT] 请求过于频繁(421)，等待180秒后继续尝试...")
                        await asyncio.sleep(180)
                        continue

                    fail_count += 1
                    logger.warning(f"[BLT] 请求返回错误状态码: {resp}, 重试 ({fail_count}/{max_retries})")
                    continue

                return resp

            except Exception as e:
                logger.warning(f"[BLT] 请求异常: {e}, 重试 ({fail_count + 1}/{max_retries})")
                fail_count += 1
                await asyncio.sleep(1)
                continue

        logger.error("[BLT] 请求重试耗尽，最终失败")
        return 500

    async def _download_image_from_url(self, url: str) -> Union[Image.Image, int]:
        """从 URL 下载图片"""
        logger.info(f"[BLT] 下载图片: {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"[BLT] 下载图片失败，状态码: {resp.status}")
                        return 500
                    image_data = await resp.read()
                    return Image.open(io.BytesIO(image_data))
        except Exception as e:
            logger.warning(f"[BLT] 下载图片失败: {e}")
            return 500

    @staticmethod
    def _decode_base64_image(base64_data: str) -> Union[Image.Image, int]:
        """解码 base64 图片数据"""
        try:
            if base64_data.startswith("data:"):
                pattern = r"data:image/([a-zA-Z+]+);base64,(.+)"
                match = re.match(pattern, base64_data)
                if match:
                    base64_data = match.group(2)

            image_bytes = base64.b64decode(base64_data)
            return Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            logger.warning(f"[BLT] 解码base64图片失败: {e}")
            return 500

    async def _parse_image_from_content(self, content: str) -> Union[Image.Image, int]:
        """从响应内容中解析图片"""
        content = content.strip()

        if content.startswith("data:") or (
            len(content) > 100 and "/" not in content and not content.startswith(("http://", "https://"))
        ):
            return self._decode_base64_image(content)

        if content.startswith(("http://", "https://")):
            return await self._download_image_from_url(content)

        logger.warning(f"[BLT] 无法识别的图片内容格式: {content[:50]}...")
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
        """调用 OpenAI 兼容 Chat Completions API 生成图片"""
        logger.info(f"[BLT] 开始生成图片: model={model}, prompt={prompt}")

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
            logger.error(f"[BLT] 图片生成失败，错误状态码: {resp}")
            return resp

        try:
            if "choices" not in resp or not resp["choices"]:
                logger.error(f"[BLT] 响应中没有choices字段: {resp}")
                return 500

            choice = resp["choices"][0]
            if "message" not in choice or "content" not in choice["message"]:
                logger.error(f"[BLT] 响应message中没有content字段: {choice}")
                return 500

            content = choice["message"]["content"]
            logger.info(f"[BLT] 获取到内容: {content[:100]}...")

            image = await self._parse_image_from_content(content)

            if isinstance(image, int):
                logger.error("[BLT] 图片解析失败")
                return image

            logger.info(f"[BLT] 图片生成成功！尺寸: {image.size}")
            return image

        except Exception as e:
            logger.error(f"[BLT] 响应解析失败: {e}")
            return 500

    async def draw_image(
        self,
        model: str,
        prompt: str,
        aspect_ratio: Optional[str] = "16:9",
        image_list: Optional[List[bytes]] = None,
    ) -> Union[Image.Image, int]:
        """调用 OpenAI DALL-E 格式 API 生成图片 (/v1/images/generations)"""
        logger.info(f"[BLT] 开始生成图片(Dall-e格式): model={model}, prompt={prompt}")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        request_body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
            "image_size": "2K",
        }

        if aspect_ratio is not None:
            request_body["aspect_ratio"] = aspect_ratio
        if image_list is not None:
            request_body["image"] = [base64.b64encode(img_bytes).decode() for img_bytes in image_list]

        resp = await self._request("POST", self.images_url, headers=headers, json=request_body)

        if isinstance(resp, int):
            logger.error(f"[BLT] 图片生成失败(Dall-e格式)，错误状态码: {resp}")
            return resp

        try:
            if "data" not in resp or not resp["data"]:
                logger.error(f"[BLT] 响应中没有data字段: {resp}")
                return 500

            data_item = resp["data"][0]

            image_content = None
            if "url" in data_item:
                image_content = data_item["url"]
            elif "b64_json" in data_item:
                image_content = f"data:image/png;base64,{data_item['b64_json']}"
            else:
                logger.error(f"[BLT] 响应data项中没有url或b64_json字段: {data_item}")
                return 500

            logger.info(f"[BLT] 获取到图片内容: {image_content[:100]}...")

            result_image = await self._parse_image_from_content(image_content)

            if isinstance(result_image, int):
                logger.error("[BLT] 图片解析失败(Dall-e格式)")
                return result_image

            logger.info(f"[BLT] 图片生成成功(Dall-e格式)！尺寸: {result_image.size}")
            return result_image

        except Exception as e:
            logger.error(f"[BLT] 响应解析失败(Dall-e格式): {e}")
            return 500


# 全局单例
blt_api = BLTAPI()
