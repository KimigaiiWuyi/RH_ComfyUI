"""MiniMax API 客户端 — 封装 MiniMax 图像生成与语音合成接口"""

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
    """MiniMax API 客户端

    封装 MiniMax 接口，支持：
    - 文生图（/v1/image_generation）
    - 图生图（image-to-image，通过 subject_reference）
    - 异步语音合成（/v1/t2a_async_v2 + /v1/query/t2a_async_query_v2）
    """

    # MiniMax T2A 支持的情绪列表
    T2A_EMOTIONS: List[str] = [
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "calm",
        "fluent",
        "whisper",
    ]

    def __init__(self) -> None:
        self.base_url: str = "https://api.minimaxi.com"
        self.generation_url = f"{self.base_url}/v1/image_generation"
        self.t2a_async_url = f"{self.base_url}/v1/t2a_async_v2"
        self.t2a_query_url = f"{self.base_url}/v1/query/t2a_async_query_v2"
        self.file_retrieve_url = f"{self.base_url}/v1/files/retrieve"
        self.file_upload_url = f"{self.base_url}/v1/files/upload"
        self.voice_clone_url = f"{self.base_url}/v1/voice_clone"

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
                    # 鉴权/余额/权限/内容审核等错误不重试
                    if status_code in (1004, 1008, 2013, 2038, 2049):
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

    # ── 文件上传与音色克隆 ──

    async def upload_file(
        self,
        file_data: bytes,
        purpose: str = "voice_clone",
        filename: str = "audio.mp3",
        content_type: str = "audio/mpeg",
    ) -> Union[int, int]:
        """上传文件到 MiniMax

        Args:
            file_data: 文件字节数据
            purpose: 用途（voice_clone 等）
            filename: 文件名
            content_type: 文件 MIME 类型

        Returns:
            file_id 或 错误状态码
        """
        logger.info(f"[MiniMax] 上传文件: {filename} ({len(file_data)} bytes)")

        try:
            form = aiohttp.FormData()
            form.add_field("purpose", purpose)
            form.add_field(
                "file",
                file_data,
                filename=filename,
                content_type=content_type,
            )

            headers = {"Authorization": f"Bearer {self.api_key}"}

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.file_upload_url,
                    headers=headers,
                    data=form,
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[MiniMax] 文件上传失败，状态码: {resp.status}")
                        return resp.status

                    resp_data = await resp.json()
                    base_resp = resp_data.get("base_resp", {})
                    if base_resp.get("status_code", 0) != 0:
                        logger.error(f"[MiniMax] 文件上传错误: {base_resp}")
                        return base_resp.get("status_code", 500)

                    file_id = resp_data.get("file", {}).get("file_id")
                    if file_id:
                        logger.info(f"[MiniMax] 文件上传成功: file_id={file_id}")
                        return int(file_id)

                    logger.error(f"[MiniMax] 文件上传响应中缺少 file_id: {resp_data}")
                    return 500

        except Exception as e:
            logger.warning(f"[MiniMax] 文件上传异常: {e}")
            return 500

    async def clone_voice(
        self,
        file_id: int,
        voice_id: str,
        text: str = "",
        model: str = "speech-2.8-hd",
    ) -> Union[bool, int]:
        """音色快速复刻

        Args:
            file_id: 待复刻音频的 file_id（通过 upload_file 获得）
            voice_id: 自定义音色 ID（8-256字符，首字母必须为英文字母）
            text: 复刻试听文本（可选，会产生额外费用）
            model: 试听使用的模型（提供 text 时必填）

        Returns:
            True 成功 或 错误状态码
        """
        logger.info(f"[MiniMax] 音色复刻: file_id={file_id}, voice_id={voice_id}")

        request_body: Dict[str, Any] = {
            "file_id": file_id,
            "voice_id": voice_id,
        }

        if text:
            request_body["text"] = text
            request_body["model"] = model

        resp = await self._request(
            "POST",
            self.voice_clone_url,
            headers=self._headers(),
            json=request_body,
        )

        if isinstance(resp, int):
            logger.error(f"[MiniMax] 音色复刻失败，状态码: {resp}")
            return resp

        base_resp = resp.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            logger.error(f"[MiniMax] 音色复刻错误: {base_resp}")
            return status_code

        input_sensitive = resp.get("input_sensitive", False)
        if input_sensitive is True:
            logger.error(f"[MiniMax] 音色复刻输入触发敏感审核: voice_id={voice_id}, resp={resp}")
            return 2013

        logger.info(f"[MiniMax] 音色复刻成功: voice_id={voice_id}, resp={resp}")
        return True

    # ── T2A 异步语音合成 ──

    async def create_t2a_async_task(
        self,
        text: str,
        voice_id: str = "audiobook_male_1",
        model: str = "speech-2.8-hd",
        speed: float = 1.0,
        vol: float = 1.0,
        pitch: int = 0,
        emotion: Optional[str] = None,
        language_boost: str = "auto",
        audio_sample_rate: int = 32000,
        bitrate: int = 128000,
        audio_format: str = "mp3",
        channel: int = 2,
    ) -> Union[Dict, int]:
        """创建 MiniMax T2A 异步语音合成任务

        Args:
            text: 待合成音频的文本（最长 5 万字符）
            voice_id: 音色编号
            model: 模型版本（speech-2.8-hd / speech-2.8-turbo 等）
            speed: 语速，范围 [0.5, 2.0]，默认 1.0
            vol: 音量，范围 (0, 10]，默认 1.0
            pitch: 语调，范围 [-12, 12]，默认 0
            emotion: 情绪控制（happy/sad/angry/fearful/disgusted/surprised/calm/fluent/whisper）
            language_boost: 语言增强，如 "auto"、"Chinese" 等
            audio_sample_rate: 采样率
            bitrate: 比特率
            audio_format: 输出格式（mp3/pcm/flac/wav 等）
            channel: 声道数（1=单声道，2=双声道）

        Returns:
            任务响应字典 或 错误状态码
        """
        logger.info(f"[MiniMax] 创建 T2A 异步任务: model={model}, text={text[:50]}...")

        voice_setting: Dict[str, Any] = {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
        }
        if emotion and emotion in self.T2A_EMOTIONS:
            voice_setting["emotion"] = emotion

        request_body: Dict[str, Any] = {
            "model": model,
            "text": text,
            "language_boost": language_boost,
            "voice_setting": voice_setting,
            "audio_setting": {
                "audio_sample_rate": audio_sample_rate,
                "bitrate": bitrate,
                "format": audio_format,
                "channel": channel,
            },
        }

        resp = await self._request(
            "POST",
            self.t2a_async_url,
            headers=self._headers(),
            json=request_body,
        )

        if isinstance(resp, int):
            logger.error(f"[MiniMax] T2A 异步任务创建失败，状态码: {resp}")
            return resp

        base_resp = resp.get("base_resp", {})
        status_code = base_resp.get("status_code", 0)
        if status_code != 0:
            logger.error(f"[MiniMax] T2A 异步任务创建失败: {base_resp}")
            return status_code

        task_id = resp.get("task_id")
        logger.info(f"[MiniMax] T2A 异步任务创建成功: task_id={task_id}")
        return resp

    async def query_t2a_async_task(
        self,
        task_id: str,
    ) -> Union[Dict, int]:
        """查询 MiniMax T2A 异步语音合成任务状态

        Args:
            task_id: 任务 ID

        Returns:
            任务状态响应字典 或 错误状态码
        """
        logger.info(f"[MiniMax] 查询 T2A 异步任务: task_id={task_id}")

        url = f"{self.t2a_query_url}?task_id={task_id}"
        resp = await self._request("GET", url, headers=self._headers(), max_retries=1)

        if isinstance(resp, int):
            logger.error(f"[MiniMax] T2A 异步任务查询失败，状态码: {resp}")
            return resp

        return resp

    async def retrieve_file(
        self,
        file_id: int,
    ) -> Union[bytes, int]:
        """下载 MiniMax 生成的文件（音频等）

        MiniMax 文件检索接口返回 JSON，其中包含实际下载 URL。
        本方法自动解析 JSON 并下载实际文件内容。

        Args:
            file_id: 文件 ID（任务完成后返回）

        Returns:
            文件字节数据 或 错误状态码
        """
        logger.info(f"[MiniMax] 下载文件: file_id={file_id}")
        url = f"{self.file_retrieve_url}?file_id={file_id}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status != 200:
                        logger.warning(f"[MiniMax] 文件下载失败，状态码: {resp.status}")
                        return resp.status

                    content_type = resp.headers.get("Content-Type", "")

                    # JSON 响应：解析下载 URL 后再下载实际文件
                    if "application/json" in content_type:
                        json_data = await resp.json()
                        logger.debug(f"[MiniMax] 文件检索响应: {json_data}")

                        base_resp = json_data.get("base_resp", {})
                        if base_resp.get("status_code", 0) != 0:
                            logger.error(f"[MiniMax] 文件检索错误: {base_resp}")
                            return base_resp.get("status_code", 500)

                        # 从响应中提取下载 URL
                        download_url = (
                            json_data.get("download_url")
                            or json_data.get("file", {}).get("download_url")
                            or json_data.get("data", {}).get("download_url")
                        )

                        if download_url:
                            logger.info(f"[MiniMax] 从 JSON 响应获取下载 URL: {download_url[:80]}...")
                            async with session.get(download_url) as dl_resp:
                                if dl_resp.status != 200:
                                    logger.warning(f"[MiniMax] 文件实际下载失败，状态码: {dl_resp.status}")
                                    return dl_resp.status
                                file_data = await dl_resp.read()
                                logger.info(f"[MiniMax] 文件下载成功: {len(file_data)} bytes")
                                return file_data

                        # 没有下载 URL，尝试直接取文件内容字段
                        file_content = json_data.get("file_content") or json_data.get("data", {}).get("content")
                        if file_content and isinstance(file_content, str):
                            import base64

                            file_data = base64.b64decode(file_content)
                            logger.info(f"[MiniMax] 从 JSON 响应解码文件: {len(file_data)} bytes")
                            return file_data

                        logger.warning(f"[MiniMax] JSON 响应中未找到下载 URL 或文件内容: {list(json_data.keys())}")
                        return 500

                    # 非 JSON 响应：直接读取文件内容
                    file_data = await resp.read()
                    logger.info(f"[MiniMax] 文件下载成功: {len(file_data)} bytes")
                    return file_data

        except Exception as e:
            logger.warning(f"[MiniMax] 文件下载异常: {e}")
            return 500

    async def generate_speech(
        self,
        text: str,
        voice_id: str = "audiobook_male_1",
        model: str = "speech-2.8-hd",
        speed: float = 1.0,
        emotion: Optional[str] = None,
        language_boost: str = "auto",
        poll_interval: float = 3.0,
        max_poll_time: float = 300.0,
    ) -> Union[bytes, int]:
        """MiniMax T2A 异步语音合成（高级接口）

        自动完成：创建任务 → 轮询状态 → 下载音频。

        Args:
            text: 待合成文本
            voice_id: 音色编号
            model: 模型版本
            speed: 语速 [0.5, 2.0]
            emotion: 情绪控制
            language_boost: 语言增强
            poll_interval: 轮询间隔（秒）
            max_poll_time: 最大等待时间（秒）

        Returns:
            音频字节数据 或 错误状态码
        """
        # 1. 创建异步任务
        resp = await self.create_t2a_async_task(
            text=text,
            voice_id=voice_id,
            model=model,
            speed=speed,
            emotion=emotion,
            language_boost=language_boost,
        )

        if isinstance(resp, int):
            return resp

        task_id = resp.get("task_id")
        if not task_id:
            logger.error("[MiniMax] T2A 任务创建响应中缺少 task_id")
            return 500

        # 2. 轮询任务状态
        elapsed = 0.0
        while elapsed < max_poll_time:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            query_resp = await self.query_t2a_async_task(str(task_id))
            if isinstance(query_resp, int):
                logger.warning(f"[MiniMax] T2A 任务查询失败: {query_resp}")
                continue

            status = query_resp.get("status", "").lower()
            logger.info(f"[MiniMax] T2A 任务状态: {status} (elapsed={elapsed:.0f}s)")

            if status == "success":
                file_id = query_resp.get("file_id")
                if not file_id:
                    logger.error("[MiniMax] T2A 任务成功但缺少 file_id")
                    return 500

                # 3. 下载音频文件
                audio_data = await self.retrieve_file(int(file_id))
                return audio_data

            if status == "failed":
                base_resp = query_resp.get("base_resp", {})
                logger.error(f"[MiniMax] T2A 任务失败: {base_resp}")
                return 500

            if status == "expired":
                logger.error("[MiniMax] T2A 任务已过期")
                return 500

        logger.error(f"[MiniMax] T2A 任务超时 ({max_poll_time}s)")
        return 500


# 全局单例
minimax_api = MiniMaxAPI()
