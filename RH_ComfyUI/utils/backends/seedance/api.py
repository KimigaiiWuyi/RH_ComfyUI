"""Seedance 2.0 ARK API 客户端

负责纯协议层:
- 任务创建(create_task)
- 任务轮询(get_task)
- 任务删除/取消(delete_task)
- 任务列表(list_tasks)
- 媒体上传(可选,目前 Seedance 接受 URL/Base64,通常无需单独上传步骤)

不在这里做任何业务参数转换,只做 HTTP 封装。
"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Optional

import httpx

from gsuid_core.logger import logger


class SeedanceAPI:
    """火山方舟 Seedance 2.0 API 客户端"""

    BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

    # ── 状态枚举 ──
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_EXPIRED = "expired"

    # ── 终态集合 ──
    TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_FAILED, STATUS_EXPIRED}

    def __init__(self, api_key: str = "", base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    def update_credentials(self, api_key: str, base_url: Optional[str] = None) -> None:
        """热更新凭证(用于配置变更)"""
        self.api_key = api_key
        if base_url:
            self.base_url = base_url.rstrip("/")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ── 创建任务 ──

    async def create_task(
        self,
        *,
        model: str,
        content: list[dict[str, Any]],
        ratio: Optional[str] = None,
        duration: Optional[int] = None,
        resolution: Optional[str] = None,
        seed: Optional[int] = None,
        generate_audio: Optional[bool] = None,
        watermark: Optional[bool] = None,
        camera_fixed: Optional[bool] = None,
        return_last_frame: Optional[bool] = None,
        service_tier: Optional[str] = None,
        execution_expires_after: Optional[int] = None,
        draft: Optional[bool] = None,
        callback_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /contents/generations/tasks

        Args:
            model: 模型 ID,如 "doubao-seedance-2-0-260128"
            content: 有序内容列表,每项含 type/text/image_url/video_url/audio_url/draft_task
            ratio: 宽高比,如 "16:9"/"adaptive"
            duration: 时长(秒)
            resolution: 480p/720p/1080p
            seed: 随机种子
            generate_audio: 是否生成有声视频
            watermark: 是否加水印
            camera_fixed: 摄像机是否固定
            return_last_frame: 是否同时返回尾帧
            service_tier: default/flex
            execution_expires_after: 任务过期秒数
            draft: 是否样片模式(仅 1.5 pro)
            callback_url: Webhook 回调地址

        Returns:
            {"id": "cgt-..."}
        """
        payload: dict[str, Any] = {
            "model": model,
            "content": content,
        }
        if ratio is not None:
            payload["ratio"] = ratio
        if duration is not None:
            payload["duration"] = duration
        if resolution is not None:
            payload["resolution"] = resolution
        if seed is not None:
            payload["seed"] = seed
        if generate_audio is not None:
            payload["generate_audio"] = generate_audio
        if watermark is not None:
            payload["watermark"] = watermark
        if camera_fixed is not None:
            payload["camera_fixed"] = camera_fixed
        if return_last_frame is not None:
            payload["return_last_frame"] = return_last_frame
        if service_tier is not None:
            payload["service_tier"] = service_tier
        if execution_expires_after is not None:
            payload["execution_expires_after"] = execution_expires_after
        if draft is not None:
            payload["draft"] = draft
        if callback_url:
            payload["callback_url"] = callback_url

        url = f"{self.base_url}/contents/generations/tasks"
        client = self._get_client()
        logger.info(f"[Seedance] 创建任务: model={model}, payload_keys={list(payload.keys())}")
        resp = await client.post(url, headers=self._auth_headers(), json=payload)
        return self._parse(resp)

    # ── 查询任务 ──

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """GET /contents/generations/tasks/{id}"""
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        client = self._get_client()
        resp = await client.get(url, headers=self._auth_headers())
        return self._parse(resp)

    # ── 删除/取消任务 ──

    async def delete_task(self, task_id: str) -> None:
        """DELETE /contents/generations/tasks/{id}"""
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        client = self._get_client()
        resp = await client.delete(url, headers=self._auth_headers())
        if resp.status_code >= 400:
            raise RuntimeError(f"Seedance 删除任务失败: {resp.status_code} {resp.text}")

    # ── 任务列表 ──

    async def list_tasks(
        self,
        *,
        page_size: int = 10,
        status: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """GET /contents/generations/tasks"""
        params: dict[str, Any] = {"page_size": page_size}
        if status:
            params["filter.status"] = status
        if model:
            params["filter.model"] = model
        url = f"{self.base_url}/contents/generations/tasks"
        client = self._get_client()
        resp = await client.get(url, headers=self._auth_headers(), params=params)
        return self._parse(resp)

    # ── 轮询辅助 ──

    async def poll_until_done(
        self,
        task_id: str,
        *,
        interval_seconds: float = 10.0,
        max_wait_seconds: float = 1800.0,
        on_progress=None,
    ) -> dict[str, Any]:
        """轮询直到任务进入终态

        Args:
            task_id: 任务 ID
            interval_seconds: 轮询间隔
            max_wait_seconds: 最大等待时长(30 分钟默认)
            on_progress: 可选的 async 回调,接收 dict 形式的当前任务状态

        Returns:
            最终的 task dict(status=succeeded/failed/expired)

        Raises:
            asyncio.TimeoutError: 超过 max_wait_seconds
            RuntimeError: status=failed 时携带 error 信息
        """
        import time

        start = time.time()
        last_status: Optional[str] = None
        while True:
            task = await self.get_task(task_id)
            status = task.get("status")
            if status != last_status:
                logger.info(f"[Seedance] 任务 {task_id} 状态变更: {last_status} → {status}")
                last_status = status

            if on_progress is not None:
                try:
                    await on_progress(task)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[Seedance] 进度回调异常: {e}")

            if status == self.STATUS_SUCCEEDED:
                return task
            if status == self.STATUS_FAILED:
                err = task.get("error") or {}
                raise RuntimeError(f"Seedance 任务失败: code={err.get('code')}, message={err.get('message')}")
            if status == self.STATUS_EXPIRED:
                raise RuntimeError(f"Seedance 任务过期: {task_id}")

            elapsed = time.time() - start
            if elapsed > max_wait_seconds:
                raise asyncio.TimeoutError(f"Seedance 任务等待超时 ({max_wait_seconds}s): {task_id}")

            await asyncio.sleep(interval_seconds)

    # ── 辅助 ──

    @staticmethod
    def bytes_to_data_url(data: bytes, mime_type: str = "application/octet-stream") -> str:
        """将字节转为 data: URL(供 Seedance content 引用)"""
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except Exception:
                err_body = {"raw": resp.text}
            raise RuntimeError(f"Seedance API 错误 {resp.status_code}: {err_body}")
        try:
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"Seedance API 返回非 JSON: {e}; raw={resp.text[:500]}")


# 全局单例(由 adapter 注入 api_key)
seedance_api = SeedanceAPI()


__all__ = ["SeedanceAPI", "seedance_api"]
