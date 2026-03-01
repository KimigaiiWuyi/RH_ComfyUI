"""
BLT 通用请求模块
提供 OpenAI 兼容 API 的通用调用接口，支持图片生成等功能
"""

import io
import re
import base64
import asyncio
from typing import Any, Dict, List, Union, Literal, Optional

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

from ...rh_config.comfyui_config import RHCOMFYUI_CONFIG

# 从配置获取
API_KEY: str = RHCOMFYUI_CONFIG.get_config("BLT_apikey").data
BASE_URL: str = RHCOMFYUI_CONFIG.get_config("BLT_API_URL").data
CHAT_COMPLETIONS_URL = f"{BASE_URL}/v1/chat/completions"
IMAGES_GENERATIONS_URL = f"{BASE_URL}/v1/images/generations"


async def _base_request(
    method: Literal["POST", "GET"],
    url: str,
    headers: Optional[Dict] = None,
    json: Optional[Dict] = None,
    data: Optional[Dict] = None,
) -> Union[Dict, int]:
    """
    基础HTTP请求函数

    Args:
        method: HTTP方法 (POST/GET)
        url: 请求URL
        headers: 请求头
        json: JSON格式请求体
        data: 表单数据请求体

    Returns:
        响应数据字典 或 错误状态码
    """
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
    method: Literal["POST", "GET"],
    url: str,
    headers: Optional[Dict] = None,
    json: Optional[Dict] = None,
    data: Optional[Dict] = None,
    max_retries: int = 3,
) -> Union[Dict, int]:
    """
    带重试机制的HTTP请求函数

    Args:
        method: HTTP方法 (POST/GET)
        url: 请求URL
        headers: 请求头
        json: JSON格式请求体
        data: 表单数据请求体
        max_retries: 最大重试次数

    Returns:
        响应数据字典 或 错误状态码 (500表示重试耗尽)
    """
    fail_count = 0

    while fail_count < max_retries:
        try:
            if not headers:
                headers = {}

            if API_KEY:
                headers["Authorization"] = f"Bearer {API_KEY}"
            else:
                logger.warning("[BLT] 未配置API_KEY，将无法请求！")
                return -1

            resp = await _base_request(method, url, headers, json, data)

            if isinstance(resp, int):
                # 421表示请求过于频繁，需要等待后重试
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


async def _download_image_from_url(url: str) -> Union[Image.Image, int]:
    """
    从URL下载图片（异步）

    Args:
        url: 图片URL

    Returns:
        PIL.Image.Image 对象 或 错误状态码
    """
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


def _decode_base64_image(base64_data: str) -> Union[Image.Image, int]:
    """
    解码base64图片数据

    Args:
        base64_data: base64编码的图片数据

    Returns:
        PIL.Image.Image 对象 或 错误状态码
    """
    try:
        # 处理可能存在的data URL前缀
        if base64_data.startswith("data:"):
            # 提取base64部分
            pattern = r"data:image/([a-zA-Z+]+);base64,(.+)"
            match = re.match(pattern, base64_data)
            if match:
                base64_data = match.group(2)

        image_bytes = base64.b64decode(base64_data)
        return Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        logger.warning(f"[BLT] 解码base64图片失败: {e}")
        return 500


async def _parse_image_from_content(content: str) -> Union[Image.Image, int]:
    """
    从响应内容中解析图片（异步）

    支持以下格式:
    1. 图片URL
    2. base64编码的图片数据
    3. data URL格式的base64数据

    Args:
        content: 响应内容字符串

    Returns:
        PIL.Image.Image 对象 或 错误状态码
    """
    content = content.strip()

    # 尝试作为base64解码
    if content.startswith("data:") or (
        len(content) > 100 and "/" not in content and not content.startswith(("http://", "https://"))
    ):
        return _decode_base64_image(content)

    # 尝试作为URL下载
    if content.startswith(("http://", "https://")):
        return await _download_image_from_url(content)

    logger.warning(f"[BLT] 无法识别的图片内容格式: {content[:50]}...")
    return 500


async def draw_image_by_model(
    model: str,
    prompt: str,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    n: Optional[int] = None,
    stream: bool = False,
    max_tokens: Optional[int] = None,
    presence_penalty: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
) -> Union[Image.Image, int]:
    """
    调用OpenAI兼容API生成图片

    Args:
        model: 要使用的模型的ID (如: gpt-4o-image)
        prompt: 生成图片的提示词
        temperature: 采样温度 (0-2)
        top_p: 核采样参数
        n: 生成数量
        stream: 是否流式响应
        max_tokens: 最大token数
        presence_penalty: 存在惩罚 (-2.0 到 2.0)
        frequency_penalty: 频率惩罚 (-2.0 到 2.0)

    Returns:
        PIL.Image.Image 对象 或 错误状态码
    """
    logger.info(f"[BLT] 开始生成图片: model={model}, prompt={prompt}")

    # 构造请求头
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # 构造请求体
    request_body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
        "stream": stream,
    }

    # 可选参数
    if temperature is not None:
        request_body["temperature"] = temperature
    if top_p is not None:
        request_body["top_p"] = top_p
    if n is not None:
        request_body["n"] = n
    if max_tokens is not None:
        request_body["max_tokens"] = max_tokens
    if presence_penalty is not None:
        request_body["presence_penalty"] = presence_penalty
    if frequency_penalty is not None:
        request_body["frequency_penalty"] = frequency_penalty

    logger.debug(f"[BLT] 请求体: {request_body}")

    # 发送请求
    resp = await _request("POST", CHAT_COMPLETIONS_URL, headers=headers, json=request_body)

    if isinstance(resp, int):
        logger.error(f"[BLT] 图片生成失败，错误状态码: {resp}")
        return resp

    try:
        # 解析响应
        if "choices" not in resp or not resp["choices"]:
            logger.error(f"[BLT] 响应中没有choices字段: {resp}")
            return 500

        choice = resp["choices"][0]
        if "message" not in choice or "content" not in choice["message"]:
            logger.error(f"[BLT] 响应message中没有content字段: {choice}")
            return 500

        content = choice["message"]["content"]
        logger.info(f"[BLT] 获取到内容: {content[:100]}...")

        # 解析图片
        image = await _parse_image_from_content(content)

        if isinstance(image, int):
            logger.error("[BLT] 图片解析失败")
            return image

        logger.info(f"[BLT] 图片生成成功！尺寸: {image.size}")
        return image

    except Exception as e:
        logger.error(f"[BLT] 响应解析失败: {e}")
        return 500


async def draw_image_by_blt(
    model: str,
    prompt: str,
    aspect_ratio: Literal["1:1", "4:3", "16:9", "9:16", "3:4", "21:9", None] = "16:9",
    image_list: Optional[List[bytes]] = None,
) -> Union[Image.Image, int]:
    """
    调用 OpenAI Dall-e 格式 API 生成图片 (/v1/images/generations)

    Args:
        model: 要使用的模型ID (如: gemini-3.1-flash-image-preview)
        prompt: 生成图片的提示词
        response_format: 返回格式 (url 或 b64_json)
        aspect_ratio: 图片宽高比 (如: 1:1, 4:3, 16:9 等)
        image: 参考图数组，格式为 list[bytes]，会自动转换为 b64_json 格式
        image_size: 图片大小 (1K, 2K, 4K, 512px)，仅部分模型支持

    Returns:
        PIL.Image.Image 对象 或 错误状态码
    """
    logger.info(f"[BLT] 开始生成图片(Dall-e格式): model={model}, prompt={prompt}")

    # 构造请求头
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # 构造请求体
    request_body: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "response_format": "url",
        "image_size": "2K",
    }

    if aspect_ratio is not None:
        request_body["aspect_ratio"] = aspect_ratio
    if image_list is not None:
        # 将 list[bytes] 转换为 base64 字符串列表
        request_body["image"] = [base64.b64encode(img_bytes).decode() for img_bytes in image_list]

    # 截断过长的 base64 字符串用于日志输出
    log_body = request_body.copy()
    if "image" in log_body:
        log_body["image"] = [f"{img[:50]}... (长度: {len(img)})" for img in log_body["image"]]

    logger.debug(f"[BLT] 请求体: {log_body}")

    # 发送请求
    resp = await _request(
        "POST",
        IMAGES_GENERATIONS_URL,
        headers=headers,
        json=request_body,
    )

    if isinstance(resp, int):
        logger.error(f"[BLT] 图片生成失败(Dall-e格式)，错误状态码: {resp}")
        return resp

    try:
        # 解析响应
        # 响应格式可能是 {"data": [{"url": "..."}]} 或 {"data": [{"b64_json": "..."}]}
        if "data" not in resp or not resp["data"]:
            logger.error(f"[BLT] 响应中没有data字段: {resp}")
            return 500

        data_item = resp["data"][0]

        # 尝试获取 url 或 b64_json
        image_content = None
        if "url" in data_item:
            image_content = data_item["url"]
        elif "b64_json" in data_item:
            # 添加 data URL 前缀以便解析
            image_content = f"data:image/png;base64,{data_item['b64_json']}"
        else:
            logger.error(f"[BLT] 响应data项中没有url或b64_json字段: {data_item}")
            return 500

        logger.info(f"[BLT] 获取到图片内容: {image_content[:100]}...")

        # 解析图片
        result_image = await _parse_image_from_content(image_content)

        if isinstance(result_image, int):
            logger.error("[BLT] 图片解析失败(Dall-e格式)")
            return result_image

        logger.info(f"[BLT] 图片生成成功(Dall-e格式)！尺寸: {result_image.size}")
        return result_image

    except Exception as e:
        logger.error(f"[BLT] 响应解析失败(Dall-e格式): {e}")
        return 500
