"""RunningHub 原生 AI 应用 API 客户端（OpenAPI v2）"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG

# RunningHub 共享型/独占型 API 并发或机器数打满时的业务码。
# 官方文档:421 TASK_QUEUE_MAXED「并发达上限，请自行排队」;
# 415 TASK_INSTANCE_MAXED「独占型机器不足，请等待后重试」。
# HTTP 常为 200 + body.code,raise_for_status 抓不到,必须看业务码。
_RH_QUEUE_FULL_CODES = frozenset({415, 421})
_RH_QUEUE_FULL_MARKERS = frozenset(
    {
        "TASK_QUEUE_MAXED",
        "TASK_INSTANCE_MAXED",
        "APIKEY_TASK_IS_RUNNING",
    }
)
_RH_QUEUE_RETRY_ATTEMPTS = 24  # 约 2~6 分钟量级,视退避而定
_RH_QUEUE_RETRY_BASE_S = 5.0
_RH_QUEUE_RETRY_MAX_S = 30.0


def _rh_business_code(data: Dict[str, Any]) -> Optional[int]:
    raw = data.get("code")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _rh_queue_full_reason(data: Dict[str, Any]) -> Optional[str]:
    """若响应表示 RH 并发/队列已满,返回可读原因;否则 None。"""
    code = _rh_business_code(data)
    msg = str(data.get("msg") or data.get("errorMessage") or data.get("message") or "")
    marker = msg.strip().upper()
    if code in _RH_QUEUE_FULL_CODES:
        return f"code={code} {msg or 'queue full'}".strip()
    for token in _RH_QUEUE_FULL_MARKERS:
        if token in marker or token in str(data).upper():
            return f"{token}: {msg}".strip(": ")
    return None


class RHAppAPI:
    """RunningHub 原生 AI 应用 API 客户端（OpenAPI v2）

    封装 RunningHub OpenAPI v2 的 AI 应用接口：
    - 获取应用节点信息
    - 上传文件
    - 提交任务
    - 查询任务状态与结果
    """

    def __init__(self) -> None:
        # 不在 __init__ 中缓存 api_key，否则用户在 Web 控制台配置后
        # 不重启进程也能立刻生效 —— 见下面 @property 的实现。
        self.base_url = "https://www.runninghub.cn"

    @property
    def api_key(self) -> str:
        """动态读取 API Key。

        必须以 property 形式读取配置，否则单例 `rh_app_api` 会在模块导入时
        把当时（很可能为空）的 key 缓存到实例属性上，后续即便用户在 Web 控制台
        写入新 key 也不会刷新，进而导致 _headers() 输出 `Bearer ` 这种
        非法头部，触发 httpx `LocalProtocolError: Illegal header value b'Bearer '`。
        """
        return SERVICE_CONFIG.get_config("RH_apikey").data or ""

    def _require_api_key(self) -> str:
        key = self.api_key
        if not key:
            raise RuntimeError(
                "[RHApp] 未配置 RunningHub API Key，请在 Web 控制台配置 RH_apikey 后重试"
            )
        return key

    def _headers(self) -> Dict[str, str]:
        # 即便上游误传空 key，也只在 key 非空时才拼 Bearer，避免 httpx 抛
        # `Illegal header value b'Bearer '`（这种异常会冒泡到远端并以
        # 不友好的 traceback 呈现给最终用户）。
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        key = self.api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def get_node_info(self, webapp_id: str) -> List[Dict[str, Any]]:
        """获取 AI 应用的可修改节点信息列表（nodeInfoList）"""
        api_key = self._require_api_key()
        url = f"{self.base_url}/api/webapp/apiCallDemo"
        params = {"apiKey": api_key, "webappId": webapp_id}

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
        api_key = self._require_api_key()
        url = f"{self.base_url}/openapi/v2/media/upload/binary"
        headers = {"Authorization": f"Bearer {api_key}"}

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

        并发已满(421 TASK_QUEUE_MAXED 等)时本地排队重试,而不是立刻报错。
        主路径依赖 core.dispatch 的 RH 共享并发闸;此处是兜底
        (多进程 / 外部占用同一 API Key / 配置大于账户配额时仍可能撞上限)。
        """
        url = f"{self.base_url}/openapi/v2/run/ai-app/{webapp_id}"

        payload: Dict[str, Any] = {
            "nodeInfoList": node_info_list,
            "instanceType": instance_type,
            "usePersonalQueue": "true" if use_personal_queue else "false",
        }

        last_reason = "queue full"
        for attempt in range(1, _RH_QUEUE_RETRY_ATTEMPTS + 1):
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                data = response.json()

            if not isinstance(data, dict):
                logger.info(f"[RHApp] 任务提交返回: {data}")
                return data  # type: ignore[return-value]

            reason = _rh_queue_full_reason(data)
            # 有 taskId 说明已入队成功,即便 body 带其它字段也不重试
            if reason is None or data.get("taskId"):
                logger.info(f"[RHApp] 任务提交返回: {data}")
                return data

            last_reason = reason
            if attempt >= _RH_QUEUE_RETRY_ATTEMPTS:
                break
            wait_s = min(_RH_QUEUE_RETRY_BASE_S * attempt, _RH_QUEUE_RETRY_MAX_S)
            logger.warning(
                f"[RHApp] RunningHub 并发/队列已满({reason}),"
                f"{wait_s:.0f}s 后排队重试({attempt}/{_RH_QUEUE_RETRY_ATTEMPTS})"
            )
            await asyncio.sleep(wait_s)

        raise RuntimeError(
            f"[RHApp] RunningHub 并发已满,排队重试 {_RH_QUEUE_RETRY_ATTEMPTS} 次仍失败: {last_reason}"
        )

    async def query_task(self, task_id: str) -> Dict[str, Any]:
        """查询任务状态和结果（OpenAPI v2）"""
        self._require_api_key()
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
        # 连续轮询失败计数(成功一次清零)。轮询是只读查询,单次网络抖动
        # (代理断连/读超时/瞬时 5xx)不代表远端任务失败 —— 任务可能已在云端
        # 跑完,直接抛错会把整单判死并退款。容忍连续 N 次再放弃。
        poll_failures = 0
        max_poll_failures = 5

        while True:
            try:
                result = await self.query_task(task_id)
                poll_failures = 0
            except httpx.HTTPStatusError as e:
                # 4xx 是确定性错误(鉴权/参数),重试无意义直接抛;5xx 按瞬时处理
                if e.response.status_code < 500:
                    raise
                result = None
                poll_error: Exception = e
            except httpx.HTTPError as e:
                result = None
                poll_error = e

            if result is None:
                poll_failures += 1
                elapsed = asyncio.get_event_loop().time() - start_time
                if poll_failures >= max_poll_failures:
                    raise RuntimeError(
                        f"[RHApp] 任务 {task_id} 轮询连续失败 {poll_failures} 次: {poll_error}"
                    ) from poll_error
                if elapsed > timeout:
                    raise TimeoutError(f"[RHApp] 任务 {task_id} 等待超时（超过 {timeout} 秒）")
                logger.warning(
                    f"[RHApp] 任务 {task_id} 轮询失败({poll_failures}/{max_poll_failures}),"
                    f"{poll_interval}s 后重试: {type(poll_error).__name__}: {poll_error}"
                )
                await asyncio.sleep(poll_interval)
                continue

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
