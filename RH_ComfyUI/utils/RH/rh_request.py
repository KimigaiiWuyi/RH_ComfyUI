import io
import uuid
import asyncio
from typing import Dict, List, Union, Literal, Optional
from pathlib import Path

import aiohttp
from PIL import Image
from aiohttp import FormData

from gsuid_core.logger import logger

from ...rh_config.comfyui_config import RHCOMFYUI_CONFIG

API_KEY: str = RHCOMFYUI_CONFIG.get_config("RH_apikey").data
BASE_URL = "https://www.runninghub.cn"

UPLOAD_URL = f"{BASE_URL}/task/openapi/upload"
APP_URL = f"{BASE_URL}/task/openapi/ai-app/run"
STATUS_URL = f"{BASE_URL}/task/openapi/status"
OUTPUT_URL = f"{BASE_URL}/task/openapi/outputs"

QUEUE = {}


async def download_image_from_url(
    url: str,
) -> Union[Image.Image, int]:
    logger.info(f"[RH] 下载图片: {url}")
    for _ in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return resp.status
                    return Image.open(io.BytesIO(await resp.read()))
        except Exception as e:
            logger.warning(f"[RH] 下载图片失败: {e}")
            continue
    return 500


async def download_video_from_url(
    url: str,
) -> Union[bytes, int]:
    logger.info(f"[RH] 下载视频: {url}")
    for _ in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return resp.status
                    return await resp.read()
        except Exception as e:
            logger.warning(f"[RH] 下载视频失败: {e}")
            continue
    return 500


async def _base_rh_requst(
    method: Literal["POST", "GET"],
    url: str,
    data: Optional[FormData] = None,
    json: Dict = {},
) -> Union[Dict, int]:
    logger.info(f"[RH] 请求: {method} {url}")

    params: dict = {}

    if json:
        json["apiKey"] = API_KEY
        params["json"] = json

    if data:
        data.add_field("apiKey", API_KEY)
        params = {"data": data}

    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, **params) as resp:
            resp = await resp.json()
            logger.info(f"[RH] 响应: {resp}")

            if resp["code"] != 0:
                return resp["code"]

            if isinstance(resp["data"], str):
                return {"data": resp["data"]}

            return resp["data"]


async def _rh_request(
    method: Literal["POST", "GET"],
    url: str,
    data: Optional[FormData] = None,
    json: Dict = {},
) -> Union[Dict, int]:
    fail_count = 0  # 用于记录非421错误的失败次数
    max_retries = 3  # 最大重试次数

    while fail_count < max_retries:
        try:
            resp = await _base_rh_requst(method, url, data, json)

            if isinstance(resp, int):
                if resp == 421:
                    logger.info("[RH] 请求过于频繁(421)，等待180秒后继续尝试...")
                    await asyncio.sleep(180)
                    continue

                fail_count += 1
                continue
            return resp

        except Exception as e:
            logger.warning(f"[RH] 请求失败: {e}")
            fail_count += 1
            continue

    return 500


def is_run_task():
    now_task = 0
    for i in QUEUE:
        if QUEUE[i] == "RUNNING":
            now_task += 1

    if now_task >= 1:
        return True

    return False


async def submit_task(webappId: str, nodeInfoList: List[Dict]) -> Union[str, int]:
    while is_run_task():
        logger.info("[RH] 任务正在运行，等待...")
        await asyncio.sleep(50)

    logger.info(f"[RH] 提交任务: {webappId}")

    data: Dict = {"nodeInfoList": nodeInfoList}
    data["webappId"] = webappId

    resp = await _rh_request("POST", APP_URL, json=data)
    if isinstance(resp, int):
        return resp

    task_id = str(resp["taskId"])
    QUEUE[webappId] = "RUNNING"
    return task_id


async def get_task_status(
    taskId: str,
) -> Union[Literal["QUEUED", "RUNNING", "FAILED", "SUCCESS"], int]:
    logger.info(f"[RH] 获取任务状态: {taskId}")
    data: Dict = {"taskId": taskId}

    resp = await _rh_request("POST", STATUS_URL, json=data)
    if isinstance(resp, int):
        return resp
    return resp["data"]


async def get_task_result(
    taskId: str,
) -> Union[str, int]:
    data: Dict = {"taskId": taskId}

    resp = await _rh_request("POST", OUTPUT_URL, json=data)
    if isinstance(resp, int):
        return resp
    return resp[0]["fileUrl"]


async def upload_file(
    file: Union[bytes, Image.Image, Path],
    fileType: str = "image",
) -> Union[str, int]:
    logger.info(f"[RH] 上传文件: {fileType}")

    if isinstance(file, Image.Image):
        file_byte_io = io.BytesIO()
        file.save(file_byte_io, format="PNG")
        file = file_byte_io.getvalue()
    elif isinstance(file, Path):
        file = file.read_bytes()

    data = FormData()

    if fileType == "image":
        content_type = "image/png"
        suffix = ".png"
    elif fileType == "audio":
        content_type = "audio/mpeg"
        suffix = ".mp3"
    else:
        content_type = "video/mp4"
        suffix = ".mp4"

    data.add_field(
        "file",
        file,
        filename=f"{uuid.uuid4().hex}{suffix}",
        content_type=content_type,
    )
    data.add_field("fileType", fileType)

    resp = await _rh_request("POST", UPLOAD_URL, data=data)
    if isinstance(resp, int):
        return resp
    return resp["fileName"]


async def get_aiapp_result(webappId: str, nodeInfoList: List[Dict]) -> Union[str, int]:
    reply = await submit_task(webappId, nodeInfoList)
    if isinstance(reply, int):
        return reply

    while True:
        status = await get_task_status(reply)
        if status == "SUCCESS":
            QUEUE[webappId] = "SUCCESS"
            return await get_task_result(reply)
        elif status == "FAILED":
            QUEUE[webappId] = "FAILED"
            return status
        await asyncio.sleep(3)
