"""RunningHub 原生 AI 应用 API 客户端（OpenAPI v2）"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import httpx

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG


class RHAppAPI:
    """RunningHub 原生 AI 应用 API 客户端（OpenAPI v2）

    封装 RunningHub OpenAPI v2 的 AI 应用接口：
    - 获取应用节点信息
    - 上传文件
    - 提交任务
    - 查询任务状态与结果
    """

    def __init__(self) -> None:
        self.api_key: str = SERVICE_CONFIG.get_config("RH_apikey").data
        self.base_url = "https://www.runninghub.cn"

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def get_node_info(self, webapp_id: str) -> List[Dict[str, Any]]:
        """获取 AI 应用的可修改节点信息列表（nodeInfoList）"""
        url = f"{self.base_url}/api/webapp/apiCallDemo"
        params = {"apiKey": self.api_key, "webappId": webapp_id}

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        node_info_list = data.get("data", {}).get("nodeInfoList", [])
        logger.info(f"[RHApp] 获取节点信息: {len(node_info_list)} 个节点")
        return node_info_list

    async def upload_file(
        self,
        file_data: bytes,
        filename: str = "input.png",
    ) -> str:
        """上传文件到 RunningHub，返回 fileName（用于 nodeInfoList.fieldValue）"""
        url = f"{self.base_url}/openapi/v2/media/upload/binary"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        files = {"file": (filename, file_data)}

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.post(url, headers=headers, files=files)
            response.raise_for_status()
            data = response.json()

        if data.get("code") != 0:
            msg = data.get("message", "未知错误")
            raise RuntimeError(f"[RHApp] 文件上传失败: {msg}")

        file_name = data.get("data", {}).get("fileName")
        if not file_name:
            raise RuntimeError(f"[RHApp] 文件上传返回异常: {data}")

        logger.info(f"[RHApp] 文件上传成功: {file_name}")
        return file_name

    async def submit_task(
        self,
        webapp_id: str,
        node_info_list: List[Dict[str, Any]],
        instance_type: str = "default",
        use_personal_queue: bool = False,
    ) -> Dict[str, Any]:
        """提交 AI 应用任务（OpenAPI v2）

        Args:
            webapp_id: AI 应用 ID
            node_info_list: 节点参数映射列表
            instance_type: 实例类型（default/plus）
            use_personal_queue: 是否使用个人独占队列
        """
        url = f"{self.base_url}/openapi/v2/run/ai-app/{webapp_id}"

        payload: Dict[str, Any] = {
            "nodeInfoList": node_info_list,
            "instanceType": instance_type,
            "usePersonalQueue": "true" if use_personal_queue else "false",
        }

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            data = response.json()

        logger.info(f"[RHApp] 任务提交返回: {data}")
        return data

    async def query_task(self, task_id: str) -> Dict[str, Any]:
        """查询任务状态和结果（OpenAPI v2）"""
        url = f"{self.base_url}/openapi/v2/query"
        payload = {"taskId": task_id}

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()

    async def wait_for_result(
        self,
        task_id: str,
        timeout: int = 600,
        poll_interval: int = 5,
    ) -> List[Dict[str, Any]]:
        """轮询等待任务完成，返回 results 列表

        Args:
            task_id: 任务 ID
            timeout: 最大等待时间（秒）
            poll_interval: 轮询间隔（秒）

        Returns:
            任务结果列表，每项包含 url/nodeId/outputType/text

        Raises:
            RuntimeError: 任务执行失败
            TimeoutError: 等待超时
        """
        start_time = asyncio.get_event_loop().time()

        while True:
            result = await self.query_task(task_id)
            status = result.get("status", "")

            if status == "SUCCESS":
                logger.success(f"[RHApp] 任务 {task_id} 完成")
                return result.get("results", [])

            if status == "FAILED":
                error_msg = result.get("errorMessage", "未知错误")
                failed_reason = result.get("failedReason", {})
                if isinstance(failed_reason, dict) and failed_reason:
                    node_name = failed_reason.get("node_name", "")
                    exception = failed_reason.get("exception_message", "")
                    traceback = failed_reason.get("traceback", "")
                    details = [f"任务失败: {error_msg}"]
                    if node_name:
                        details.append(f"节点: {node_name}")
                    if exception:
                        details.append(f"原因: {exception}")
                    if traceback:
                        details.append(f"Traceback: {traceback}")
                    error_msg = " | ".join(details)
                raise RuntimeError(error_msg)

            # 超时检查
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"[RHApp] 任务 {task_id} 等待超时（超过 {timeout} 秒）")

            status_text = {
                "QUEUED": "排队中",
                "RUNNING": "运行中",
                "PENDING": "等待中",
            }.get(status, status)
            logger.info(f"[RHApp] 任务 {task_id} {status_text}...")
            await asyncio.sleep(poll_interval)


# 全局单例
rh_app_api = RHAppAPI()
