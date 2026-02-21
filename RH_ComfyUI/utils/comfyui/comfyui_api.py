import io
import json
import uuid
import asyncio
from typing import Dict, List, Union, Optional
from pathlib import Path
from collections import defaultdict

import httpx
import websockets
from PIL import Image
from websockets import ClientConnection

from gsuid_core.logger import logger

from ..resource.RESOURCE_PATH import OUTPUT_PATH
from ...rh_config.comfyui_config import RHCOMFYUI_CONFIG

API_KEY: str = RHCOMFYUI_CONFIG.get_config("RH_apikey").data
BASE_URL: str = RHCOMFYUI_CONFIG.get_config("ComfyUI_BaseURL").data


class ComfyUIAPI:
    def __init__(self) -> None:
        if "runninghub" in BASE_URL.lower():
            self.server_address = f"www.runninghub.cn/proxy/{API_KEY}"
            self.url = f"https://www.runninghub.cn/proxy/{API_KEY}"
        else:
            self.server_address = BASE_URL
            self.url = f"http://{BASE_URL}"

        self.client_id = str(uuid.uuid4())
        self.ws: Optional[ClientConnection] = None  # 2. 初始化 ws 为 None
        self.is_prompt = False
        self._prompt_events = defaultdict(asyncio.Queue)  # 1. 使用Queue来分发消息
        self._listener_task = None  # 用于持有监听任务

    async def connect(self):
        """
        3. 创建一个异步方法来建立连接
        """
        if self.ws and self.ws.state == websockets.State.OPEN:
            logger.info("WebSocket is already connected.")
            return

        try:
            # 使用 wss:// 协议以适配 HTTPS 代理
            ws_protocol = "wss://" if "runninghub" in self.server_address.lower() else "ws://"
            ws_url = f"{ws_protocol}{self.server_address}/ws?clientId={self.client_id}"
            logger.info(ws_url)
            self.ws = await websockets.connect(ws_url, max_size=None)  # max_size=None for large data
            if self.ws and not self._listener_task:
                self._listener_task = asyncio.create_task(self._ws_listener())
            logger.info(f"WebSocket connected to {ws_url}")
        except Exception as e:
            logger.info(f"Failed to connect WebSocket: {e}")
            self.ws = None

    async def get_history(self, prompt_id: str):
        url = f"{self.url}/history/{prompt_id}"
        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            result = response.json()
            logger.info(result)
            return result

    async def queue_prompt(self, prompt: Dict):
        if not self.ws or self.ws.state != websockets.State.OPEN:
            await self.connect()

        p = {"prompt": prompt, "client_id": self.client_id}
        headers = {"Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            req = await client.post(f"{self.url}/prompt", json=p, headers=headers)
            req.raise_for_status()  # Good practice to check for errors
            prompt_data = req.json()
        logger.info(f"Prompt ID: {prompt_data}")
        return prompt_data

    def save_image(self, images: List, output_path: Path, image_name: str):
        for itm in images:
            if itm["type"] != "output":
                continue
            output_path.mkdir(parents=True, exist_ok=True)
            image = Image.open(io.BytesIO(itm["image_data"]))
            image.save(output_path / f"{image_name}.jpg", "JPEG")
            return image

    def save_video(self, videos: List, output_path: Path, image_name: str):
        for itm in videos:
            if itm["type"] != "output":
                continue
            output_path.mkdir(parents=True, exist_ok=True)
            video_data = io.BytesIO(itm["image_data"])
            with open(output_path / f"{image_name}.mp4", "wb") as f:
                f.write(video_data.getbuffer())

    async def get_image(self, filename: str, subfolder: Path, folder_type):
        url = f"{self.url}/view"
        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": folder_type,
        }

        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            return response.content

    async def get_videos(self, prompt_id: str):
        output_audios = []
        history = (await self.get_history(prompt_id))[prompt_id]
        for node_id in history["outputs"]:
            node_output = history["outputs"][node_id]
            for content in ["gifs", "images"]:
                if content in node_output:
                    for video in node_output[content]:
                        if video["type"] == "output":
                            video_data = await self.get_file(
                                video["filename"],
                                video["subfolder"],
                                video["type"],
                            )
                        output_audios.append({"filename": video["filename"], "data": video_data})
        return output_audios

    async def get_images(self, prompt_id):
        output_images = []

        history = (await self.get_history(prompt_id))[prompt_id]
        for node_id in history["outputs"]:
            node_output = history["outputs"][node_id]
            output_data = {}
            if "images" in node_output:
                for image in node_output["images"]:
                    if image["type"] == "output":
                        image_data = await self.get_image(
                            image["filename"],
                            image["subfolder"],
                            image["type"],
                        )
                        output_data["image_data"] = image_data
                        output_data["file_name"] = image["filename"]
                        output_data["type"] = image["type"]
                        output_images.append(output_data)

        return output_images

    async def get_audios(self, prompt_id: str):
        output_audios = []
        history = (await self.get_history(prompt_id))[prompt_id]
        for node_id in history["outputs"]:
            node_output = history["outputs"][node_id]
            for content in ["audio", "images"]:
                if content in node_output:
                    for audio in node_output[content]:
                        if audio["type"] == "output":
                            audio_data = await self.get_file(
                                audio["filename"],
                                audio["subfolder"],
                                audio["type"],
                            )
                        output_audios.append({"filename": audio["filename"], "data": audio_data})
        return output_audios

    async def get_file(self, filename: str, subfolder, folder_type):
        # 确保 subfolder 是 Path 对象
        if isinstance(subfolder, str):
            subfolder = Path(subfolder)

        # 先尝试本地文件访问
        file_path = subfolder / filename

        # 检查文件是否存在
        if file_path.exists():
            # 直接读取本地文件
            try:
                with open(file_path, "rb") as f:
                    return f.read()
            except Exception as e:
                raise IOError(f"读取本地音频文件失败: {file_path}, 错误: {e}")

        # 如果本地文件不存在，尝试网络访问
        url = f"{self.url}/view"
        # 修复路径编码问题：将 Windows 路径分隔符转换为正斜杠
        subfolder_str = str(subfolder).replace("\\", "/")
        params = {
            "filename": filename,
            "subfolder": subfolder_str,
            "type": folder_type,
        }

        # 添加重试机制
        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
                    response = await client.get(url, params=params, timeout=10.0)
                    response.raise_for_status()
                    return response.content
            except httpx.HTTPStatusError as e:
                if attempt == max_retries - 1:  # 最后一次尝试
                    logger.info(f"获取音频文件失败，URL: {url}, 参数: {params}, 错误: {e}")
                    raise
                logger.info(f"获取音频文件失败，正在重试 ({attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(1)  # 等待1秒后重试
            except Exception as e:
                logger.info(f"获取音频文件时发生未知错误: {e}")
                raise

    async def get_texts(self, prompt_id):
        output_texts: list[str] = []

        history = (await self.get_history(prompt_id))[prompt_id]
        for node_id in history["outputs"]:
            node_output = history["outputs"][node_id]
            if "text" in node_output:
                output_texts.extend(node_output["text"])
        return output_texts

    async def generate_text_by_prompt(
        self,
        prompt: Dict,
    ):
        logger.debug(f"🚧 [ComfyUI] 生成文本提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        texts = await self.get_texts(prompt_id)
        logger.info(f"✅ [ComfyUI] 文本生成完成！文本内容: {texts}")
        return texts

    async def generate_audio_by_prompt(
        self,
        prompt: Dict,
        output_path: Optional[Path] = None,
        file_name: Optional[str] = None,
    ) -> Optional[bytes]:
        if output_path is None:
            output_path = OUTPUT_PATH

        if file_name is None:
            file_name = f"{uuid.uuid4()}.mp3"

        logger.debug(f"🚧 [ComfyUI] 生成音频提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        audios = await self.get_audios(prompt_id)
        logger.info(f"✅ [ComfyUI] 音频生成完成！包含音频数量: {len(audios)}")
        if audios and len(audios) > 0:
            audio_object = audios[0]  # 取第一个音频文件
            audio_data: bytes = audio_object["data"]  # 获取音频数据
            audio_path = output_path / file_name
            with open(audio_path, "wb") as f:
                f.write(audio_data)
            logger.info(f"✅ [ComfyUI] 音频生成完成！保存路径: {audio_path}")
            return audio_data
        return None

    async def generate_image_by_prompt(
        self,
        prompt: Dict,
        output_path: Optional[Path] = None,
        image_name: Optional[str] = None,
    ):
        if image_name is None:
            image_name = f"{uuid.uuid4()}.png"
        if output_path is None:
            output_path = OUTPUT_PATH

        logger.debug(f"🚧 [ComfyUI] 生成图片提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        images = await self.get_images(prompt_id)
        image = self.save_image(images, output_path, image_name)
        if image is None:
            raise ValueError("🚫 [ComfyUI失败] 未知原因生成失败！")
        if self.is_prompt:
            while self.is_prompt:
                await asyncio.sleep(5)
        logger.info(f"✅ [ComfyUI] 图片生成完成！图片路径: {image}")
        return image

    async def generate_video_by_prompt(
        self,
        prompt: Dict,
        output_path: Optional[Path] = None,
        video_name: Optional[str] = None,
    ) -> Optional[bytes]:
        if video_name is None:
            video_name = f"{uuid.uuid4()}.mp4"
        if output_path is None:
            output_path = OUTPUT_PATH

        logger.debug(f"🚧 [ComfyUI] 生成视频提示词: {prompt}")

        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        videos = await self.get_videos(prompt_id)

        logger.info(f"✅ [ComfyUI] 视频生成完成！包含视频数量: {len(videos)}")
        if videos and len(videos) > 0:
            video_object = videos[0]  # 取第一个视频文件
            video_data: bytes = video_object["data"]  # 获取视频数据
            video_path = output_path / video_name
            with open(video_path, "wb") as f:
                f.write(video_data)
            logger.info(f"✅ [ComfyUI] 视频生成完成！保存路径: {video_path}")
            return video_data
        return None

    async def upload_mp3(self, mp3: Union[Path, bytes]) -> str:
        return await self.upload_image(mp3, "audio/mpeg")

    async def upload_image(
        self,
        image_path: Union[Path, Image.Image, bytes],
        type: str = "image/png",
    ) -> str:
        if type == "audio/mpeg":
            suffix = "mp3"
        else:
            suffix = "png"

        if isinstance(image_path, Image.Image):
            image_bytes = io.BytesIO()
            image_path.save(image_bytes, format="PNG")
            image_bytes.seek(0)
            image_name = f"{uuid.uuid4()}.{suffix}"
        elif isinstance(image_path, bytes):
            image_bytes = io.BytesIO(image_path)
            image_name = f"{uuid.uuid4()}.{suffix}"
        else:
            with open(image_path, "rb") as file:
                image_bytes = file.read()
            image_name = image_path.name

        files = {
            "image": (image_name, image_bytes, type),
            "type": (None, "input"),
            "overwrite": (None, "true"),
        }

        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.post(f"{self.url}/upload/image", files=files)
            try:
                upload_name = response.json()["name"]
                return upload_name
            except:  # noqa: E722
                logger.info(response.text)
                return ""

    async def _ws_listener(self):
        """
        唯一的、持续从 WebSocket 接收消息的后台任务。
        """
        if not self.ws:
            return
        logger.info("WebSocket listener started.")
        try:
            while True:
                try:
                    message = await self.ws.recv()
                    data = json.loads(message)

                    # 检查消息中是否有 prompt_id，以便分发
                    prompt_id = data.get("data", {}).get("prompt_id")
                    if prompt_id:
                        # 将消息放入对应 prompt_id 的队列中
                        await self._prompt_events[prompt_id].put(data)

                    # 特殊处理执行完成的信号，它没有嵌套在 data 里
                    if data.get("type") == "executing" and data.get("data", {}).get("node") is None:
                        prompt_id = data.get("data", {}).get("prompt_id")
                        if prompt_id:
                            await self._prompt_events[prompt_id].put(data)

                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"WebSocket connection closed: {e}. Reconnecting...")
                    await self.connect()  # 尝试重连
                except Exception as e:
                    logger.info(f"Error in WebSocket listener: {e}")
                    if self.ws is None:
                        self._prompt_events.clear()
                        break
                    await asyncio.sleep(1)  # 避免错误循环过快
        finally:
            logger.info("WebSocket listener stopped.")

    async def track_progress(self, prompt, prompt_id):
        """
        不再直接 recv，而是从自己的队列里获取消息。
        """
        q = self._prompt_events[prompt_id]
        try:
            while True:
                message = await q.get()  # 从队列中获取属于自己的消息

                logger.debug(f"Prompt {prompt_id} -> {message}")

                if message["type"] == "progress":
                    data = message["data"]
                    current_step = data["value"]
                    logger.debug(f"Prompt {prompt_id} -> Step: {current_step} of: {data['max']}")

                # 当收到执行完成的信号时，任务结束
                if message.get("type") == "executing" and message.get("data", {}).get("node") is None:
                    logger.success(f"Prompt {prompt_id} finished.")
                    break  # 退出循环
        finally:
            # 清理，防止内存泄漏
            del self._prompt_events[prompt_id]

    async def reboot(self):
        if self.is_prompt:
            while self.is_prompt:
                await asyncio.sleep(5)

        try:
            url = f"{self.url}/api/manager/reboot"
            httpx.get(url)
        except:  # noqa: E722
            pass

        await asyncio.sleep(60)
        while True:
            try:
                self.__init__()
                break
            except Exception as e:
                logger.warning(f"❌ [ComfyUI] 重启ComfyUI失败: {e}")
                await asyncio.sleep(40)


api = ComfyUIAPI()
