"""进行中生成任务注册表 — 本地取消 + 可选上游 cancel + vendor_task_id 落库

设计:
- ``dispatch()`` 进入执行时把当前 asyncio.Task 登记进来(按 record_id / trace_id 索引);
- 异步后端创建成功后 ``bind_vendor_task`` 写入 vendor_task_id(供 resume_poll);
  若上游支持 cancel,再附 ``cancel_remote``;
- ``cancel_generation``:有 remote 则先 await(带超时),再 ``task.cancel()``,
  dispatch 走 CancelledError → 退款路径。

进程内有效;多 worker 需粘性路由到执行 worker,或宿主凭 vendor_task_id 直连上游 cancel。
resume_poll 由宿主传入 vendor_task_id,不依赖 extra_params 落库。
"""

from __future__ import annotations

import json
import asyncio
from typing import Any
from dataclasses import field, dataclass
from collections.abc import Callable, Awaitable

from gsuid_core.logger import logger

CancelRemote = Callable[[], Awaitable[None]]

# 上游 cancel 挂起时不应拖死本地 cancel_generation HTTP
REMOTE_CANCEL_TIMEOUT_S = 15.0


@dataclass
class ActiveGeneration:
    """一次正在执行的 dispatch / resume 任务句柄。"""

    model_name: str
    trace_id: str = ""
    record_id: int | None = None
    user_id: str = ""
    task: asyncio.Task[Any] | None = None
    vendor_task_id: str | None = None
    channel_name: str = ""
    cancel_remote: CancelRemote | None = None
    remote_cancel_attempted: bool = False
    # rh_app 等:可 resume 不可 cancel
    allow_cancel: bool = True
    # wire 镜像:ContextVar 丢失时(子任务) record_task 仍可读最终载荷
    wire_prompt: str | None = None
    wire_request: Any | None = None
    _token: int = field(default=0, repr=False)


class ActiveTaskRegistry:
    """进程级进行中任务表。"""

    def __init__(self) -> None:
        self._by_record: dict[int, ActiveGeneration] = {}
        self._by_trace: dict[str, ActiveGeneration] = {}
        # 当前 Task id → ActiveGeneration(provider 无 ctx 时绑定 vendor task)
        self._local: dict[int, ActiveGeneration] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    def _next_token(self) -> int:
        self._seq += 1
        return self._seq

    async def register(
        self,
        *,
        model_name: str,
        trace_id: str = "",
        record_id: int | None = None,
        user_id: str = "",
        task: asyncio.Task[Any] | None = None,
        allow_cancel: bool = True,
    ) -> ActiveGeneration:
        """登记一次进行中的生成。"""
        ag = ActiveGeneration(
            model_name=model_name,
            trace_id=(trace_id or "").strip(),
            record_id=record_id,
            user_id=user_id or "",
            task=task or asyncio.current_task(),
            allow_cancel=allow_cancel,
            _token=self._next_token(),
        )
        async with self._lock:
            if ag.record_id is not None:
                self._by_record[ag.record_id] = ag
            if ag.trace_id:
                self._by_trace[ag.trace_id] = ag
            t = ag.task
            if t is not None:
                self._local[id(t)] = ag
        return ag

    async def unregister(self, ag: ActiveGeneration) -> None:
        async with self._lock:
            if ag.record_id is not None and self._by_record.get(ag.record_id) is ag:
                self._by_record.pop(ag.record_id, None)
            if ag.trace_id and self._by_trace.get(ag.trace_id) is ag:
                self._by_trace.pop(ag.trace_id, None)
            t = ag.task
            if t is not None and self._local.get(id(t)) is ag:
                self._local.pop(id(t), None)

    def current(self) -> ActiveGeneration | None:
        """取当前协程登记的 ActiveGeneration(无则 None)。"""
        t = asyncio.current_task()
        if t is None:
            return None
        return self._local.get(id(t))

    async def bind_vendor_task(
        self,
        *,
        vendor_task_id: str,
        channel_name: str = "",
        cancel_remote: CancelRemote | None = None,
        ag: ActiveGeneration | None = None,
    ) -> None:
        """写入上游 task_id 并 await 落库;可选挂远程 cancel。

        ``cancel_remote=None`` 时只持久化 id(如 rh_app 可 resume 但无 cancel API)。
        """
        target = ag or self.current()
        if target is None:
            logger.debug(f"[ActiveTasks] bind_vendor_task 时无进行中任务,忽略: vendor_task_id={vendor_task_id}")
            return
        target.vendor_task_id = vendor_task_id
        if cancel_remote is not None:
            target.cancel_remote = cancel_remote
        if channel_name:
            target.channel_name = channel_name
        logger.info(
            f"[ActiveTasks] 已绑定 vendor 任务: model={target.model_name}, "
            f"vendor_task_id={vendor_task_id}, channel={target.channel_name}, "
            f"has_remote_cancel={cancel_remote is not None}, "
            f"trace_id={target.trace_id}, record_id={target.record_id}"
        )
        if target.record_id is not None:
            await _persist_vendor_task_id(
                record_id=int(target.record_id),
                vendor_task_id=vendor_task_id,
                channel_name=channel_name or target.channel_name,
            )

    async def bind_vendor_cancel(
        self,
        *,
        vendor_task_id: str,
        cancel_remote: CancelRemote | None = None,
        channel_name: str = "",
        ag: ActiveGeneration | None = None,
    ) -> None:
        """兼容别名:等同 ``bind_vendor_task``(cancel_remote 可省略)。"""
        await self.bind_vendor_task(
            vendor_task_id=vendor_task_id,
            cancel_remote=cancel_remote,
            channel_name=channel_name,
            ag=ag,
        )

    def get_by_trace(self, trace_id: str) -> ActiveGeneration | None:
        return self._by_trace.get((trace_id or "").strip())

    def get_by_record(self, record_id: int) -> ActiveGeneration | None:
        return self._by_record.get(int(record_id))

    async def cancel(
        self,
        *,
        trace_id: str | None = None,
        record_id: int | None = None,
        reason: str = "user_cancel",
    ) -> dict[str, Any]:
        """取消一次进行中生成。

        Returns:
            dict: ok / found / cancelled_local / cancelled_remote / model / vendor_task_id / message
        """
        ag: ActiveGeneration | None = None
        if record_id is not None:
            ag = self.get_by_record(int(record_id))
        if ag is None and trace_id:
            ag = self.get_by_trace(trace_id)
        if ag is None:
            return {
                "ok": False,
                "found": False,
                "cancelled_local": False,
                "cancelled_remote": False,
                "model": "",
                "vendor_task_id": "",
                "message": "未找到进行中的任务(可能已结束或不在本进程)",
            }

        if not ag.allow_cancel:
            return {
                "ok": False,
                "found": True,
                "cancelled_local": False,
                "cancelled_remote": False,
                "model": ag.model_name,
                "vendor_task_id": ag.vendor_task_id or "",
                "channel": ag.channel_name,
                "trace_id": ag.trace_id,
                "record_id": ag.record_id,
                "message": "该任务不支持取消(如 rh_app 仅可 resume 继续轮询)",
            }

        # 已结束后禁止 remote DELETE + ok=true,避免与 dispatch commit 竞态双成功
        t = ag.task
        if t is not None and t.done():
            logger.info(
                f"[ActiveTasks] 取消跳过(任务已结束): model={ag.model_name}, "
                f"trace_id={ag.trace_id}, record_id={ag.record_id}, "
                f"vendor_task_id={ag.vendor_task_id or ''}, channel={ag.channel_name}"
            )
            return {
                "ok": False,
                "found": True,
                "cancelled_local": False,
                "cancelled_remote": False,
                "model": ag.model_name,
                "vendor_task_id": ag.vendor_task_id or "",
                "channel": ag.channel_name,
                "trace_id": ag.trace_id,
                "record_id": ag.record_id,
                "message": "任务已结束,无法取消",
            }

        remote_ok = False
        remote_err: str | None = None
        remote_skip: str = ""
        has_remote = ag.cancel_remote is not None
        logger.info(
            f"[ActiveTasks] 开始取消: model={ag.model_name}, "
            f"trace_id={ag.trace_id}, record_id={ag.record_id}, "
            f"vendor_task_id={ag.vendor_task_id or ''}, channel={ag.channel_name or ''}, "
            f"has_remote_cancel={has_remote}, reason={reason}"
        )

        # remote 前再确认一次未终态(避免对刚完成任务发 DELETE)
        t = ag.task
        if t is not None and t.done():
            logger.info(
                f"[ActiveTasks] 取消前任务已结束,跳过上游: model={ag.model_name}, "
                f"vendor_task_id={ag.vendor_task_id or ''}"
            )
            return {
                "ok": False,
                "found": True,
                "cancelled_local": False,
                "cancelled_remote": False,
                "model": ag.model_name,
                "vendor_task_id": ag.vendor_task_id or "",
                "channel": ag.channel_name,
                "trace_id": ag.trace_id,
                "record_id": ag.record_id,
                "message": "任务已结束,无法取消",
            }

        if ag.cancel_remote is not None and not ag.remote_cancel_attempted:
            ag.remote_cancel_attempted = True
            logger.info(
                f"[ActiveTasks] 请求上游 cancel: model={ag.model_name}, "
                f"vendor_task_id={ag.vendor_task_id}, channel={ag.channel_name}, "
                f"timeout={REMOTE_CANCEL_TIMEOUT_S:.0f}s"
            )
            try:
                await asyncio.wait_for(
                    ag.cancel_remote(),
                    timeout=REMOTE_CANCEL_TIMEOUT_S,
                )
                remote_ok = True
                logger.info(
                    f"[ActiveTasks] 上游取消成功: model={ag.model_name}, "
                    f"vendor_task_id={ag.vendor_task_id}, channel={ag.channel_name}, "
                    f"reason={reason}"
                )
            except TimeoutError:
                remote_err = f"timeout>{REMOTE_CANCEL_TIMEOUT_S:.0f}s"
                logger.warning(
                    f"[ActiveTasks] 上游取消超时(继续本地取消): "
                    f"model={ag.model_name}, vendor_task_id={ag.vendor_task_id}, "
                    f"channel={ag.channel_name}"
                )
            except Exception as exc:  # noqa: BLE001 — 上游失败仍要取消本地
                remote_err = str(exc)
                logger.warning(
                    f"[ActiveTasks] 上游取消失败(继续本地取消): "
                    f"model={ag.model_name}, vendor_task_id={ag.vendor_task_id}, "
                    f"channel={ag.channel_name}: {exc}"
                )
        elif ag.cancel_remote is None:
            if ag.vendor_task_id:
                remote_skip = "已有 vendor_task_id 但通道未挂 cancel_remote(供应商不支持或未实现)"
            else:
                remote_skip = "尚未 bind 上游任务(create 未完成或失败),无法 remote cancel"
            logger.info(
                f"[ActiveTasks] 跳过上游 cancel: model={ag.model_name}, "
                f"trace_id={ag.trace_id}, record_id={ag.record_id}, "
                f"vendor_task_id={ag.vendor_task_id or ''}, channel={ag.channel_name or ''}, "
                f"reason={remote_skip}"
            )
        elif ag.remote_cancel_attempted:
            remote_skip = "本任务已尝试过 remote cancel"
            logger.info(
                f"[ActiveTasks] 跳过重复上游 cancel: model={ag.model_name}, "
                f"vendor_task_id={ag.vendor_task_id or ''}"
            )

        # remote 期间任务可能已完成:再检查一次,避免对已成功任务报 cancel ok
        local_ok = False
        t = ag.task
        if t is not None and t.done():
            logger.info(
                f"[ActiveTasks] remote 后任务已结束: model={ag.model_name}, "
                f"cancelled_remote={remote_ok}, vendor_task_id={ag.vendor_task_id or ''}"
            )
            return {
                "ok": False,
                "found": True,
                "cancelled_local": False,
                "cancelled_remote": remote_ok,
                "model": ag.model_name,
                "vendor_task_id": ag.vendor_task_id or "",
                "channel": ag.channel_name,
                "trace_id": ag.trace_id,
                "record_id": ag.record_id,
                "remote_skip": remote_skip,
                "message": ("任务在取消过程中已结束" + ("(上游已尝试 cancel)" if remote_ok else "")),
            }
        if t is not None and not t.done():
            t.cancel()
            local_ok = True
            logger.info(
                f"[ActiveTasks] 已 cancel asyncio.Task: model={ag.model_name}, "
                f"trace_id={ag.trace_id}, record_id={ag.record_id}, "
                f"cancelled_remote={remote_ok}, reason={reason}"
            )

        msg = "已请求取消"
        if remote_ok:
            msg += "(含上游)"
        elif remote_err:
            msg += f"(上游失败: {remote_err})"
        elif remote_skip:
            msg += f"(无上游: {remote_skip})"
        if not local_ok and not remote_ok:
            msg = "任务已结束或无法取消"

        return {
            "ok": local_ok or remote_ok,
            "found": True,
            "cancelled_local": local_ok,
            "cancelled_remote": remote_ok,
            "model": ag.model_name,
            "vendor_task_id": ag.vendor_task_id or "",
            "channel": ag.channel_name,
            "trace_id": ag.trace_id,
            "record_id": ag.record_id,
            "remote_skip": remote_skip,
            "remote_error": remote_err or "",
            "message": msg,
        }


_REGISTRY = ActiveTaskRegistry()


_EXTRA_PARAMS_MAX = 2048


def _dump_extra_params(extra: dict[str, Any]) -> str:
    return json.dumps(extra, ensure_ascii=False)


def _serialize_extra_params_keeping_vendor(
    extra: dict[str, Any],
    *,
    vendor_task_id: str,
    channel_name: str = "",
) -> str:
    """序列化 extra_params,优先保证 vendor_task_id / vendor_channel 完整可解析。

    禁止对 JSON 字符串盲目 ``[:2048]``(会截断成非法 JSON,丢 resume 键)。
    超长时先丢其它字段,仍超长则只写 vendor 两键。
    """
    extra["vendor_task_id"] = vendor_task_id
    if channel_name:
        extra["vendor_channel"] = channel_name
    elif "vendor_channel" not in extra:
        pass

    serialized = _dump_extra_params(extra)
    if len(serialized) <= _EXTRA_PARAMS_MAX:
        return serialized

    compact: dict[str, Any] = {"vendor_task_id": vendor_task_id}
    ch = channel_name or str(extra.get("vendor_channel") or "")
    if ch:
        compact["vendor_channel"] = ch
    for key, value in extra.items():
        if key in compact:
            continue
        trial = dict(compact)
        trial[key] = value
        if len(_dump_extra_params(trial)) <= _EXTRA_PARAMS_MAX:
            compact[key] = value
    serialized = _dump_extra_params(compact)
    if len(serialized) <= _EXTRA_PARAMS_MAX:
        return serialized
    # vendor_task_id 本身异常过长时仍保证合法 JSON(截断值而非截断文档)
    tid = vendor_task_id[:512]
    only: dict[str, Any] = {"vendor_task_id": tid}
    if ch:
        only["vendor_channel"] = str(ch)[:64]
    return _dump_extra_params(only)


async def _persist_vendor_task_id(
    *,
    record_id: int,
    vendor_task_id: str,
    channel_name: str = "",
) -> None:
    """把上游 task_id 合并进 RHComfyuiTaskRecord.extra_params_json(失败仅日志)。"""
    if record_id <= 0 or not vendor_task_id:
        return
    try:
        from sqlmodel import col, select

        from gsuid_core.utils.database.base_models import async_maker

        from ...utils.database.models import RHComfyuiTaskRecord

        async with async_maker() as session:
            stmt = select(RHComfyuiTaskRecord).where(col(RHComfyuiTaskRecord.id) == record_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return
            extra: dict[str, Any] = {}
            raw = (row.extra_params_json or "").strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        extra = parsed
                except (TypeError, ValueError):
                    pass
            row.extra_params_json = _serialize_extra_params_keeping_vendor(
                extra,
                vendor_task_id=vendor_task_id,
                channel_name=channel_name,
            )
            session.add(row)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — 统计增强不得影响主流程
        logger.debug(f"[ActiveTasks] persist vendor_task_id 失败(忽略): record_id={record_id}: {exc}")


def get_active_task_registry() -> ActiveTaskRegistry:
    return _REGISTRY


def remote_cancel_already_attempted() -> bool:
    """当前协程任务是否已尝试过上游 cancel(供 CancelledError 兜底跳过二次 DELETE)。"""
    ag = _REGISTRY.current()
    return ag is not None and ag.remote_cancel_attempted


async def cancel_generation(
    *,
    trace_id: str | None = None,
    record_id: int | None = None,
    reason: str = "user_cancel",
) -> dict[str, Any]:
    """公开取消入口(core / RH_ComfyUI.api / HTTP 共用)。"""
    if not trace_id and record_id is None:
        return {
            "ok": False,
            "found": False,
            "cancelled_local": False,
            "cancelled_remote": False,
            "model": "",
            "vendor_task_id": "",
            "message": "必须提供 trace_id 或 record_id",
        }
    return await _REGISTRY.cancel(trace_id=trace_id, record_id=record_id, reason=reason)


__all__ = [
    "ActiveGeneration",
    "ActiveTaskRegistry",
    "CancelRemote",
    "REMOTE_CANCEL_TIMEOUT_S",
    "get_active_task_registry",
    "remote_cancel_already_attempted",
    "cancel_generation",
    "_serialize_extra_params_keeping_vendor",
]
