"""ComfyUI WebSocket API 客户端 — 从原 comfyui_api.py 迁移"""

from __future__ import annotations

import io
import json
import uuid
import asyncio
from typing import Any, Dict, List, Union, Optional
from pathlib import Path
from collections import defaultdict

import httpx
import websockets
from PIL import Image
from websockets import ClientConnection

from gsuid_core.logger import logger

from ...resource.RESOURCE_PATH import OUTPUT_PATH
from ....rh_config.comfyui_config import SERVICE_CONFIG

# ── RunningHub 代理模式轮询配置 ────────────────────────────────────────────────
# 轮询每 5s 一次:
# - _RUNNINGHUB_MAX_POLLS = 600  → 最长等待约 50 分钟
# - _RUNNINGHUB_WARN_AFTER = 12  → 连续 ~60s 无产物时打印 warning 快照
# - _RUNNINGHUB_FAIL_AFTER = 120 → 连续 ~600s(10 分钟)无产物判定为失败
# 修改此处即可调整超时/告警阈值,无需改动 _poll_history_until_complete 主体。
_RUNNINGHUB_MAX_POLLS = 600
_RUNNINGHUB_WARN_AFTER = 12
_RUNNINGHUB_FAIL_AFTER = 120

# RunningHub /openapi/v2/query / 通用 REST 风格响应中的失败状态(大写比较)
_RUNNINGHUB_FAILURE_STATUSES = frozenset({
    "FAILED",
    "ERROR",
    "CANCELLED",
    "TIMEOUT",
    "INTERRUPTED",
})

# ComfyUI /history 风格响应中嵌套 status.status_str 的失败状态(小写比较)
_RUNNINGHUB_FAILURE_NESTED_STATUSES = frozenset({
    "failed",
    "error",
    "cancelled",
    "timeout",
    "interrupted",
})


class ComfyUIAPI:
    """ComfyUI WebSocket API 客户端"""

    def __init__(self) -> None:
        # ⚠️ 不要在 __init__ 里缓存 base_url / api_key / url / server_address。
        # Web 控制台改了 `ComfyUI_BaseURL` 或 `RH_apikey` 之后,这些字段必须立刻
        # 反映新值,否则下一次请求会带着旧的 host 或旧的 runninghub proxy 路径。
        # 全部用 property 按需重读 SERVICE_CONFIG。
        self.client_id = str(uuid.uuid4())
        self.ws: Optional[ClientConnection] = None
        self.is_prompt = False
        self._prompt_events: dict[str, asyncio.Queue[Any]] = defaultdict(asyncio.Queue)
        self._listener_task: Optional[asyncio.Task[None]] = None

        # mapper 可在调用时设置一个"下一帧使用的工作流文件名"覆盖;
        # adapter 会在生成上传图片后重新加载该工作流。None 表示无覆盖。
        self.workflow_override: Optional[str] = None

    # ── 动态配置字段(任何修改都会在下次访问时生效) ──

    @property
    def _raw_base_url(self) -> str:
        return SERVICE_CONFIG.get_config("ComfyUI_BaseURL").data or ""

    @property
    def _raw_api_key(self) -> str:
        return SERVICE_CONFIG.get_config("RH_apikey").data or ""

    @property
    def api_key(self) -> str:
        """动态读取 RunningHub API Key。

        RunningHub 代理模式 (url 形如 ``runninghub.cn/proxy/{key}``) 需要把
        key 嵌进 URL,因此 api_key 一变,URL 也得跟着重算 —— 见 ``url`` /
        ``server_address`` 两个 property。
        """
        return self._raw_api_key

    @property
    def is_runninghub(self) -> bool:
        return "runninghub" in self._raw_base_url.lower()

    @property
    def server_address(self) -> str:
        if self.is_runninghub:
            return f"www.runninghub.cn/proxy/{self._raw_api_key}"
        return self._raw_base_url.removeprefix("http://").removeprefix("https://")

    @property
    def url(self) -> str:
        if self.is_runninghub:
            return f"https://www.runninghub.cn/proxy/{self._raw_api_key}"
        base_url = self._raw_base_url
        return base_url if base_url.startswith(("http://", "https://")) else f"http://{base_url}"

    def set_workflow_override(self, workflow_filename: str) -> None:
        """指定本次生成使用的工作流文件(覆盖 YAML 中的默认 workflow)

        mapper 可以在检查到当前默认工作流与请求的输入档案不匹配时
        (例如 0 张图 vs 1+ 张图)调用本方法;adapter 在上传图片后
        会优先加载本文件名指定的工作流。

        Args:
            workflow_filename: 仅文件名,例如 "wan2.2_i2v.json"。
        """
        self.workflow_override = workflow_filename

    def consume_workflow_override(self) -> Optional[str]:
        """读取并清空当前的工作流覆盖

        adapter 在准备阶段调用一次,避免影响下一次生成。
        """
        name = self.workflow_override
        self.workflow_override = None
        return name

    async def connect(self) -> None:
        """建立 WebSocket 连接"""
        if self.ws and self.ws.state == websockets.State.OPEN:
            logger.info("WebSocket is already connected.")
            return

        try:
            ws_protocol = "wss://" if "runninghub" in self.server_address.lower() else "ws://"
            ws_url = f"{ws_protocol}{self.server_address}/ws?clientId={self.client_id}"
            logger.info(ws_url)
            self.ws = await websockets.connect(ws_url, max_size=None)
            if self.ws and not self._listener_task:
                self._listener_task = asyncio.create_task(self._ws_listener())
            logger.info(f"WebSocket connected to {ws_url}")
        except Exception as e:
            logger.info(f"Failed to connect WebSocket: {e}")
            self.ws = None

    async def get_history(self, prompt_id: str, *, log_result: bool = True) -> Dict[str, Any]:
        url = f"{self.url}/history/{prompt_id}"
        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            result = response.json()
            if log_result:
                if result:
                    logger.info(result)
                else:
                    logger.debug(f"Prompt {prompt_id} history is empty.")
            return result

    async def queue_prompt(self, prompt: Dict[str, Any]) -> Dict[str, Any]:
        if not self.is_runninghub and (not self.ws or self.ws.state != websockets.State.OPEN):
            await self.connect()

        p = {"prompt": prompt, "client_id": self.client_id}
        headers = {"Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            req = await client.post(f"{self.url}/prompt", json=p, headers=headers)
            req.raise_for_status()
            prompt_data = req.json()
        logger.info(f"Prompt ID: {prompt_data}")
        # RunningHub 代理在失败时返回 HTTP 200 + {"code":...,"msg":"NOT_FOUND",...},
        # raise_for_status() 抓不到;若无 prompt_id 直接抛错,避免下游
        # prompt_data["prompt_id"] 抛出难以定位的 KeyError。
        if not isinstance(prompt_data, dict) or "prompt_id" not in prompt_data:
            raise RuntimeError(f"[ComfyUI] 提交工作流失败,响应缺少 prompt_id: {prompt_data}")
        return prompt_data

    def save_image(self, images: List[Dict[str, Any]], output_path: Path, image_name: str) -> Optional[Image.Image]:
        for itm in images:
            if itm["type"] != "output":
                continue
            output_path.mkdir(parents=True, exist_ok=True)
            image = Image.open(io.BytesIO(itm["image_data"]))
            image.save(output_path / f"{image_name}.jpg", "JPEG")
            return image
        return None

    def save_video(self, videos: List[Dict[str, Any]], output_path: Path, image_name: str) -> None:
        for itm in videos:
            if itm["type"] != "output":
                continue
            output_path.mkdir(parents=True, exist_ok=True)
            video_data = io.BytesIO(itm["image_data"])
            with open(output_path / f"{image_name}.mp4", "wb") as f:
                f.write(video_data.getbuffer())

    async def get_image(self, filename: str, subfolder: Path, folder_type: str) -> bytes:
        url = f"{self.url}/view"
        params: dict[str, Any] = {
            "filename": filename,
            "subfolder": str(subfolder),
            "type": folder_type,
        }
        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.get(url, params=params, timeout=10.0)
            response.raise_for_status()
            return response.content

    async def get_videos(self, prompt_id: str) -> List[Dict[str, Any]]:
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

    async def get_images(self, prompt_id: str) -> List[Dict[str, Any]]:
        output_images = []
        history = (await self.get_history(prompt_id))[prompt_id]
        for node_id in history["outputs"]:
            node_output = history["outputs"][node_id]
            output_data: Dict[str, Any] = {}
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

    async def get_audios(self, prompt_id: str) -> List[Dict[str, Any]]:
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

    async def get_file(self, filename: str, subfolder, folder_type: str) -> bytes:
        if isinstance(subfolder, str):
            subfolder = Path(subfolder)

        file_path = subfolder / filename
        if file_path.exists():
            with open(file_path, "rb") as f:
                return f.read()

        url = f"{self.url}/view"
        subfolder_str = str(subfolder).replace("\\", "/")
        params = {
            "filename": filename,
            "subfolder": subfolder_str,
            "type": folder_type,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
                    response = await client.get(url, params=params, timeout=10.0)
                    response.raise_for_status()
                    return response.content
            except httpx.HTTPStatusError as e:
                if attempt == max_retries - 1:
                    logger.info(f"获取文件失败，URL: {url}, 参数: {params}, 错误: {e}")
                    raise
                logger.info(f"获取文件失败，正在重试 ({attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.info(f"获取文件时发生未知错误: {e}")
                raise

        raise RuntimeError(f"获取文件失败: {filename}")

    async def get_texts(self, prompt_id: str) -> list[str]:
        output_texts: list[str] = []
        history = (await self.get_history(prompt_id))[prompt_id]
        for node_id in history["outputs"]:
            node_output = history["outputs"][node_id]
            if "text" in node_output:
                output_texts.extend(node_output["text"])
        return output_texts

    async def generate_text_by_prompt(self, prompt: Dict[str, Any]) -> list[str]:
        logger.debug(f"🚧 [ComfyUI] 生成文本提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        texts = await self.get_texts(prompt_id)
        logger.info(f"✅ [ComfyUI] 文本生成完成！文本内容: {texts}")
        return texts

    async def generate_audio_by_prompt(
        self,
        prompt: Dict[str, Any],
        output_path: Optional[Path] = None,
        file_name: Optional[str] = None,
    ) -> Optional[bytes]:
        resolved_output_path: Path = output_path if output_path is not None else OUTPUT_PATH
        resolved_file_name: str = file_name if file_name is not None else f"{uuid.uuid4()}.mp3"

        logger.debug(f"🚧 [ComfyUI] 生成音频提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        audios = await self.get_audios(prompt_id)
        logger.info(f"✅ [ComfyUI] 音频生成完成！包含音频数量: {len(audios)}")
        if audios and len(audios) > 0:
            audio_object = audios[0]
            audio_data: bytes = audio_object["data"]
            audio_path = resolved_output_path / resolved_file_name
            with open(audio_path, "wb") as f:
                f.write(audio_data)
            logger.info(f"✅ [ComfyUI] 音频生成完成！保存路径: {audio_path}")
            return audio_data
        return None

    async def generate_image_by_prompt(
        self,
        prompt: Dict[str, Any],
        output_path: Optional[Path] = None,
        image_name: Optional[str] = None,
    ) -> Image.Image:
        resolved_image_name: str = image_name if image_name is not None else f"{uuid.uuid4()}.png"
        resolved_output_path: Path = output_path if output_path is not None else OUTPUT_PATH

        logger.debug(f"🚧 [ComfyUI] 生成图片提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        images = await self.get_images(prompt_id)
        image = self.save_image(images, resolved_output_path, resolved_image_name)
        if image is None:
            raise ValueError("🚫 [ComfyUI失败] 未知原因生成失败！")
        if self.is_prompt:
            while self.is_prompt:
                await asyncio.sleep(5)
        logger.info(f"✅ [ComfyUI] 图片生成完成！图片路径: {image}")
        return image

    async def generate_video_by_prompt(
        self,
        prompt: Dict[str, Any],
        output_path: Optional[Path] = None,
        video_name: Optional[str] = None,
    ) -> Optional[bytes]:
        resolved_video_name: str = video_name if video_name is not None else f"{uuid.uuid4()}.mp4"
        resolved_output_path: Path = output_path if output_path is not None else OUTPUT_PATH

        logger.debug(f"🚧 [ComfyUI] 生成视频提示词: {prompt}")
        prompt_data = await self.queue_prompt(prompt)
        prompt_id = prompt_data["prompt_id"]
        await self.track_progress(prompt, prompt_id)
        videos = await self.get_videos(prompt_id)

        logger.info(f"✅ [ComfyUI] 视频生成完成！包含视频数量: {len(videos)}")
        if videos and len(videos) > 0:
            video_object = videos[0]
            video_data: bytes = video_object["data"]
            video_path = resolved_output_path / resolved_video_name
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
            buffer = io.BytesIO()
            image_path.save(buffer, format="PNG")
            data = buffer.getvalue()
            image_name = f"{uuid.uuid4()}.{suffix}"
        elif isinstance(image_path, bytes):
            data = image_path
            image_name = f"{uuid.uuid4()}.{suffix}"
        else:
            data = image_path.read_bytes()
            image_name = image_path.name

        # RunningHub 代理不暴露 ComfyUI 原生 POST /upload/image(会返回
        # {"code":404,"msg":"NOT_FOUND"}),必须改走 OpenAPI v2 二进制上传接口。
        if self.is_runninghub:
            return await self._upload_image_runninghub(data, image_name)

        files = {
            "image": (image_name, data, type),
            "type": (None, "input"),
            "overwrite": (None, "true"),
        }

        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.post(f"{self.url}/upload/image", files=files)
            try:
                return response.json()["name"]
            except Exception as exc:
                # 以前这里静默 return "",导致工作流带着空 LoadImage 提交,
                # 最终以难以定位的 KeyError: 'prompt_id' 收场。改为抛出明确错误。
                logger.error(f"[ComfyUI] 图片上传失败({response.status_code}): {response.text}")
                raise RuntimeError(f"[ComfyUI] 图片上传失败: {response.text}") from exc

    async def _upload_image_runninghub(self, data: bytes, filename: str) -> str:
        """RunningHub 代理模式下上传图片,返回可供 LoadImage 引用的 fileName。

        RunningHub 代理并不提供 ComfyUI 原生的 ``/upload/image``,而是通过
        OpenAPI v2 的二进制上传接口接收媒体(见 ``rh_app/api.py``)。
        """
        key = self._raw_api_key
        if not key:
            raise RuntimeError("[ComfyUI] 未配置 RunningHub API Key(RH_apikey),无法上传图片")

        url = "https://www.runninghub.cn/openapi/v2/media/upload/binary"
        headers = {"Authorization": f"Bearer {key}"}
        files = {"file": (filename, data)}

        async with httpx.AsyncClient(timeout=6000, follow_redirects=True) as client:
            response = await client.post(url, headers=headers, files=files)
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != 0:
            raise RuntimeError(f"[ComfyUI] RunningHub 图片上传失败: {payload}")

        file_name = payload.get("data", {}).get("fileName")
        if not file_name:
            raise RuntimeError(f"[ComfyUI] RunningHub 图片上传返回异常: {payload}")

        logger.info(f"[ComfyUI] RunningHub 图片上传成功: {file_name}")
        return file_name

    async def _ws_listener(self) -> None:
        """从 WebSocket 接收消息的后台任务"""
        ws = self.ws
        if ws is None:
            return
        logger.info("WebSocket listener started.")
        try:
            while True:
                try:
                    message = await ws.recv()
                    data = json.loads(message)

                    prompt_id = data.get("data", {}).get("prompt_id")
                    if prompt_id:
                        await self._prompt_events[prompt_id].put(data)

                    if data.get("type") == "executing" and data.get("data", {}).get("node") is None:
                        prompt_id = data.get("data", {}).get("prompt_id")
                        if prompt_id:
                            await self._prompt_events[prompt_id].put(data)

                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"WebSocket connection closed: {e}. Reconnecting...")
                    self.ws = None
                    await self.connect()
                except Exception as e:
                    logger.info(f"Error in WebSocket listener: {e}")
                    if self.ws is None:
                        self._prompt_events.clear()
                        break
                    await asyncio.sleep(1)
        finally:
            logger.info("WebSocket listener stopped.")

    async def track_progress(self, prompt: Dict[str, Any], prompt_id: str) -> None:
        """等待任务完成。

        本地 ComfyUI 优先使用 WebSocket 事件；RunningHub 代理的 WebSocket 可能主动返回 1011，
        因此 RunningHub 模式改为轮询 /history，避免因 WS 断开影响生成流程。
        """
        if self.is_runninghub:
            await self._poll_history_until_complete(prompt_id)
            return

        q = self._prompt_events[prompt_id]
        try:
            while True:
                message = await q.get()
                logger.debug(f"Prompt {prompt_id} -> {message}")

                if message["type"] == "progress":
                    data = message["data"]
                    current_step = data["value"]
                    logger.debug(f"Prompt {prompt_id} -> Step: {current_step} of: {data['max']}")

                if message.get("type") == "executing" and message.get("data", {}).get("node") is None:
                    logger.success(f"Prompt {prompt_id} finished.")
                    break
        finally:
            del self._prompt_events[prompt_id]

    async def _poll_history_until_complete(self, prompt_id: str) -> None:
        """通过 /history 轮询等待 RunningHub 代理任务完成

        关键设计:
        - empty_streak 仅在 history 真正包含产物 (outputs 非空) 时才清零。
          RunningHub 代理在任务失败时常返回 {"<prompt_id>": {"outputs": {}, ...}}
          这种"伪运行中"响应,如果按 prompt_id 是否存在来清零会导致
          fallback 永远不触发,因此改为按"是否有产物"清零。
        - 持续空响应 / 无产物达到 WARN_AFTER 时主动打印 warning 快照,
          方便排查"卡在 history 哪一步"。
        - 持续无产物达到 FAIL_AFTER 时直接抛出 RuntimeError,
          不再依赖已经失效的 /openapi/v2/query fallback
          (代理模式下 prompt_id ≠ RunningHub OpenAPI v2 taskId,
          原 fallback 永远查不到任何东西)。
        - 超时由内建 TimeoutError 改为 RuntimeError,与下游
          `except Exception` 的失败传播路径一致。
        - 网络 / 解析异常从 debug 提升为 warning,生产环境可见。

        日志策略:
        - 启动 / 完成 / 异常 → info
        - 每 ~30s 心跳 / 空响应告警 → info
        - 其它轮询 → debug
        """
        empty_streak = 0
        logger.info(f"[ComfyUI] 开始轮询任务 {prompt_id} 状态 (RunningHub 代理模式)")
        for i in range(_RUNNINGHUB_MAX_POLLS):
            try:
                history = await self.get_history(prompt_id, log_result=False)
                self._raise_for_runninghub_failed_history(prompt_id, history)
                # 任务完成:outputs 非空
                if prompt_id in history and history[prompt_id].get("outputs"):
                    logger.success(f"Prompt {prompt_id} finished by history polling.")
                    return
                # 阶段化日志:启动 + 30s/60s/... 心跳都打 info,避免长任务时调用方以为卡死
                if i == 0:
                    logger.info(f"[ComfyUI] 任务 {prompt_id} 已提交,等待代理返回 history")
                elif i % 6 == 0:
                    logger.info(
                        f"[ComfyUI] 任务 {prompt_id} 仍在生成中,"
                        f"已轮询 {i} 次(约 {i * 5}s),empty_streak={empty_streak}"
                    )
                else:
                    logger.debug(f"[ComfyUI] 轮询任务 {prompt_id} 状态中...")
                # empty_streak 仅在历史记录确实有产物时才清零;
                # 其余情形(history 空、不含 prompt_id、记录存在但 outputs 为空)都累加。
                if prompt_id in history and history[prompt_id].get("outputs"):
                    empty_streak = 0
                else:
                    empty_streak += 1
                # 持续无产物:达到告警阈值时打印 history 快照,便于排查
                if empty_streak == _RUNNINGHUB_WARN_AFTER or (
                    empty_streak > _RUNNINGHUB_WARN_AFTER
                    and empty_streak % (_RUNNINGHUB_WARN_AFTER * 2) == 0
                ):
                    logger.warning(
                        f"[ComfyUI] 任务 {prompt_id} 已连续 {empty_streak} 次"
                        f"(约 {empty_streak * 5}s)未拿到产物,深入检查 history"
                    )
                    if prompt_id in history:
                        record = history[prompt_id]
                        snapshot = {
                            k: record.get(k) for k in ("status", "outputs", "messages")
                        }
                        logger.warning(
                            f"[ComfyUI] 任务 {prompt_id} history 快照: {snapshot}"
                        )
                # 持续无产物达到失败阈值:主动判定失败,不再无限轮询
                if empty_streak >= _RUNNINGHUB_FAIL_AFTER:
                    raise RuntimeError(
                        f"RunningHub 任务 {prompt_id} 失败 - 代理 history 连续 "
                        f"{empty_streak} 次(约 {empty_streak * 5}s)未返回产物,"
                        f"判定为无响应失败"
                    )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"[ComfyUI] 任务 {prompt_id} history HTTP 错误: {e}"
                )
                empty_streak += 1
            except RuntimeError:
                raise
            except Exception as e:
                logger.warning(
                    f"[ComfyUI] 任务 {prompt_id} history 解析错误: {e}"
                )
                empty_streak += 1
            await asyncio.sleep(5)
        raise RuntimeError(
            f"RunningHub 任务 {prompt_id} 等待生成结果超时 "
            f"(已轮询 {_RUNNINGHUB_MAX_POLLS} 次 / 约 {_RUNNINGHUB_MAX_POLLS * 5}s)"
        )

    @staticmethod
    def _raise_for_runninghub_failed_history(prompt_id: str, history: Dict[str, Any]) -> None:
        """识别 RunningHub history 中的失败响应,并抛出明确错误。

        兼容多种响应格式:
        - RunningHub /openapi/v2/query 风格: 顶层 status/errorMessage/failedReason/code
        - ComfyUI /history 风格: {prompt_id: {status: {status_str: "failed"}}}
        - 通用 REST 错误: 顶层 success: false / errorCode

        识别大小写不敏感的失败标志:
        FAILED / ERROR / CANCELLED / TIMEOUT / INTERRUPTED
        同时识别顶层 code != 0 / success: false / errorCode 非 0 / errorMessage 单独存在。
        """
        # ── 顶层 status (RunningHub /openapi/v2/query / 通用 REST 格式) ──
        status = history.get("status")
        if isinstance(status, str):
            normalized = status.strip().upper()
            if normalized in _RUNNINGHUB_FAILURE_STATUSES:
                error_message = history.get("errorMessage")
                failed_reason = history.get("failedReason")
                if isinstance(failed_reason, dict):
                    exception_type = failed_reason.get("exception_type")
                    exception_message = failed_reason.get("exception_message")
                    node_name = failed_reason.get("node_name")
                    node_id = failed_reason.get("node_id")
                    details = [f"RunningHub 任务 {prompt_id} 失败 ({normalized})"]
                    if isinstance(error_message, str) and error_message:
                        details.append(error_message)
                    if isinstance(exception_type, str) and exception_type:
                        details.append(exception_type)
                    if isinstance(exception_message, str) and exception_message:
                        details.append(exception_message)
                    if isinstance(node_name, str) and node_name:
                        if isinstance(node_id, str) and node_id:
                            details.append(f"失败节点: {node_name}({node_id})")
                        else:
                            details.append(f"失败节点: {node_name}")
                    raise RuntimeError(" - ".join(details))

                if isinstance(error_message, str) and error_message:
                    raise RuntimeError(
                        f"RunningHub 任务 {prompt_id} 失败 ({normalized}) - {error_message}"
                    )

                raise RuntimeError(f"RunningHub 任务 {prompt_id} 失败 ({normalized})")

        # ── 顶层 code != 0 ──
        code = history.get("code")
        if isinstance(code, int) and code != 0:
            msg = history.get("msg") or history.get("message") or "未知错误"
            raise RuntimeError(f"RunningHub 任务 {prompt_id} 失败 (code={code}) - {msg}")

        # ── 顶层 success: false ──
        success = history.get("success")
        if success is False:
            msg = (
                history.get("errorMessage")
                or history.get("msg")
                or history.get("message")
                or "未知错误"
            )
            raise RuntimeError(f"RunningHub 任务 {prompt_id} 失败 - {msg}")

        # ── 顶层 errorCode 非 0 / errorMessage 单独存在 ──
        error_code = history.get("errorCode")
        if error_code and error_code != 0:
            msg = history.get("errorMessage") or "未知错误"
            raise RuntimeError(
                f"RunningHub 任务 {prompt_id} 失败 (errorCode={error_code}) - {msg}"
            )
        if (
            not status
            and isinstance(history.get("errorMessage"), str)
            and history["errorMessage"]
        ):
            raise RuntimeError(
                f"RunningHub 任务 {prompt_id} 失败 - {history['errorMessage']}"
            )

        # ── 嵌套 status (ComfyUI /history 格式) ──
        prompt_data = history.get(prompt_id)
        if isinstance(prompt_data, dict):
            nested_status = prompt_data.get("status")
            # 嵌套 status 可能是 dict 或 字符串
            if isinstance(nested_status, dict):
                status_str = (nested_status.get("status_str") or "").strip().lower()
                if status_str in _RUNNINGHUB_FAILURE_NESTED_STATUSES:
                    messages = nested_status.get("messages") or []
                    error_msg = (
                        " | ".join(str(m) for m in messages) if messages else "未知原因"
                    )
                    raise RuntimeError(
                        f"RunningHub 任务 {prompt_id} 失败 ({status_str}) - {error_msg}"
                    )
            elif isinstance(nested_status, str):
                normalized_nested = nested_status.strip().lower()
                if normalized_nested in _RUNNINGHUB_FAILURE_NESTED_STATUSES:
                    raise RuntimeError(
                        f"RunningHub 任务 {prompt_id} 失败 ({normalized_nested})"
                    )

    async def reboot(self) -> None:
        if self.is_prompt:
            while self.is_prompt:
                await asyncio.sleep(5)

        try:
            url = f"{self.url}/api/manager/reboot"
            httpx.get(url)
        except Exception:
            pass

        await asyncio.sleep(60)
        while True:
            try:
                self.__init__()
                break
            except Exception as e:
                logger.warning(f"❌ [ComfyUI] 重启ComfyUI失败: {e}")
                await asyncio.sleep(40)


# 全局单例
api = ComfyUIAPI()
