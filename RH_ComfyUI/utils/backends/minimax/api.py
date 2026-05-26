"""MiniMax 图像生成 API 客户端 — 封装 MiniMax /v1/image_generation 接口"""

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


class MiniMaxAPI:
    """MiniMax 图像生成 API 客户端

    封装 MiniMax /v1/image_generation 接口，支持：
    - 文生图（text-to-image）
    - 图生图（image-to-image，通过 subject_reference）
    """

    def __init__(self) -> None:
        self.base_url: str = "https://api.minimaxi.com"
        self.generation_url = f"{self.base_url}/v1/image_generation"

    @property
    def api_key(self) -> str:
        """动态读取 API Key，避免模块导入时配置未生效"""
        return RHCOMFYUI_CONFIG.get_config("MiniMax_apikey").data or ""

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def _base_request(
        self,
        method: Literal["POST", "GET"],
        url: str,
        headers: Optional[Dict] = None,
        json: Optional[Dict] = None,
    ) -> Union[Dict, int]:
        """基础 HTTP 请求"""
        logger.info(f"[MiniMax] 请求: {method} {url}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers, json=json) as resp:
                    logger.info(f"[MiniMax] 响应状态: {resp.status}")

                    if resp.status != 200:
                        return resp.status

                    resp_data = await resp.json()
                    logger.debug(f"[MiniMax] 响应数据: {resp_data}")
                    return resp_data

        except Exception as e:
            logger.warning(f"[MiniMax] 请求失败: {e}")
            return 500

    async def _request(
        self,
        method: Literal["POST", "GET"],
        url: str,
        headers: Optional[Dict] = None,
        json: Optional[Dict] = None,
        max_retries: int = 3,
    ) -> Union[Dict, int]:
        """带重试机制的 HTTP 请求"""
        fail_count = 0

        while fail_count < max_retries:
            try:
                if not headers:
                    headers = {}

                if not self.api_key:
                    logger.warning("[MiniMax] 未配置 API Key，将无法请求！")
                    return -1

                headers.setdefault("Authorization", f"Bearer {self.api_key}")

                resp = await self._base_request(method, url, headers, json)

                if isinstance(resp, int):
                    if resp == 429:
                        logger.info("[MiniMax] 请求过于频繁(429)，等待60秒后继续尝试...")
                        await asyncio.sleep(60)
                        continue

                    fail_count += 1
                    logger.warning(f"[MiniMax] 请求返回错误状态码: {resp}, 重试 ({fail_count}/{max_retries})")
                    continue

                # 检查 MiniMax 业务错误码
                base_resp = resp.get("base_resp", {})
                status_code = base_resp.get("status_code", 0)
                if status_code != 0:
                    status_msg = base_resp.get("status_msg", "未知错误")
                    logger.error(f"[MiniMax] 业务错误: code={status_code}, msg={status_msg}")
                    # 限流错误重试
                    if status_code == 1002:
                        logger.info("[MiniMax] 触发限流，等待60秒后重试...")
                        await asyncio.sleep(60)
                        continue
                    # 鉴权/余额等错误不重试
                    if status_code in (1004, 1008, 2049):
                        return resp
                    fail_count += 1
                    continue

                return resp

            except Exception as e:
                logger.warning(f"[MiniMax] 请求异常: {e}, 重试 ({fail_count + 1}/{max_retries})")
                fail_count += 1
                await asyncio.sleep(1)
                continue

        logger.error("[MiniMax] 请求重试耗尽，最终失败")
        return 500

    async def _download_image_from_url(self, url: str) -> Union[Image.Image, int]:
        """从 URL 下载图片"""
        logger.info(f"[MiniMax] 下载图片: {url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"[MiniMax] 下载图片失败，状态码: {resp.status}")
                        return 500
                    image_data = await resp.read()
                    return Image.open(io.BytesIO(image_data))
        except Exception as e:
            logger.warning(f"[MiniMax] 下载图片失败: {e}")
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
            logger.warning(f"[MiniMax] 解码 base64 图片失败: {e}")
            return 500

    def _calculate_aspect_ratio(self, width: int, height: int) -> str:
        """根据宽高计算最接近的 MiniMax 支持的宽高比"""
        actual_ratio = width / height
        ratios = {
            "21:9": 21 / 9,
            "16:9": 16 / 9,
            "4:3": 4 / 3,
            "3:2": 3 / 2,
            "1:1": 1 / 1,
            "2:3": 2 / 3,
            "3:4": 3 / 4,
            "9:16": 9 / 16,
        }
        closest = min(ratios.keys(), key=lambda k: abs(ratios[k] - actual_ratio))
        return closest

    def _encode_image_to_base64(self, image_data: bytes, mime: str = "image/jpeg") -> str:
        """将图片 bytes 编码为 data URL"""
        b64 = base64.b64encode(image_data).decode("utf-8")
        return f"data:{mime};base64,{b64}"

    async def generate_image(
        self,
        prompt: str,
        model: str = "image-01",
        aspect_ratio: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        n: int = 1,
        seed: Optional[int] = None,
        prompt_optimizer: bool = False,
        subject_reference: Optional[List[Dict[str, str]]] = None,
    ) -> Union[List[Image.Image], int]:
        """调用 MiniMax /v1/image_generation 生成图片

        Args:
            prompt: 图像文本描述（最长 1500 字符）
            model: 模型名称（image-01 / image-01-live）
            aspect_ratio: 宽高比（如 "1:1", "16:9" 等）
            width: 图片宽度（仅 image-01，512-2048，8 的倍数）
            height: 图片高度（仅 image-01，512-2048，8 的倍数）
            n: 生成数量（1-9）
            seed: 随机种子
            prompt_optimizer: 是否开启 prompt 自动优化
            subject_reference: 图生图的人物主体参考列表

        Returns:
            图片列表 或 错误状态码
        """
        logger.info(f"[MiniMax] 开始生成图片: model={model}, prompt={prompt[:50]}...")

        request_body: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "response_format": "url",
            "prompt_optimizer": prompt_optimizer,
        }

        if aspect_ratio:
            request_body["aspect_ratio"] = aspect_ratio
        elif width and height:
            request_body["width"] = width
            request_body["height"] = height

        if seed is not None:
            request_body["seed"] = seed

        if subject_reference:
            request_body["subject_reference"] = subject_reference

        resp = await self._request("POST", self.generation_url, headers=self._headers(), json=request_body)

        if isinstance(resp, int):
            logger.error(f"[MiniMax] 图片生成失败，错误状态码: {resp}")
            return resp

        try:
            data = resp.get("data", {})
            image_urls = data.get("image_urls", [])
            image_base64_list = data.get("image_base64", [])

            if not image_urls and not image_base64_list:
                logger.error(f"[MiniMax] 响应中没有图片数据: {resp}")
                return 500

            images: List[Image.Image] = []

            # 优先使用 URL
            if image_urls:
                for url in image_urls:
                    img = await self._download_image_from_url(url)
                    if isinstance(img, int):
                        logger.warning(f"[MiniMax] 下载图片失败: {url}")
                        continue
                    images.append(img)
            elif image_base64_list:
                for b64_data in image_base64_list:
                    img = self._decode_base64_image(b64_data)
                    if isinstance(img, int):
                        logger.warning("[MiniMax] 解码 base64 图片失败")
                        continue
                    images.append(img)

            metadata = resp.get("metadata", {})
            success_count = metadata.get("success_count", len(images))
            failed_count = metadata.get("failed_count", 0)
            logger.info(f"[MiniMax] 图片生成成功！成功: {success_count}, 失败: {failed_count}")

            if not images:
                logger.error("[MiniMax] 所有图片均下载/解码失败")
                return 500

            return images

        except Exception as e:
            logger.error(f"[MiniMax] 响应解析失败: {e}")
            return 500


# 全局单例
minimax_api = MiniMaxAPI()
