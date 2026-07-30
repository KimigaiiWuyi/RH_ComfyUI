"""record_dispatch — 调度器侧的统计/落盘封装

职责(全部失败不影响主流程):
1. begin_dispatch() 预扣后插入 status=running
2. 产物落盘 OUTPUT_PATH
3. record_dispatch() 终态 UPDATE/INSERT,补 entry_point / channel
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional
from dataclasses import field, dataclass

from gsuid_core.logger import logger

from ..schema.types import NodeOutput
from ..schema.request import GenerationResult, GenerationRequest

if TYPE_CHECKING:
    from ..base.generation import AIGCGenerationBase
    from ..dispatch.context import DispatchContext


@dataclass
class _RecordNode:
    """record_task 期望的 NodeDef 形状(duck-typing 最小面)"""

    name: str
    backend: str
    point_cost: int
    provider: str = ""
    backend_model: str = ""
    backend_models: dict[str, str] = field(default_factory=dict)


def _node_view(model: "AIGCGenerationBase", output: Optional[NodeOutput], point_cost: Optional[int]) -> _RecordNode:
    """把模型实例投影为统计所需的节点视图。"""
    node = model.node
    channel = ""
    vendor_model = ""
    if output is not None:
        channel = str(output.metadata.get("channel", ""))
        vendor_model = str(output.metadata.get("vendor_model", ""))
    cost = point_cost if point_cost is not None else model.point_cost
    if node is not None:
        return _RecordNode(
            name=node.name,
            backend=node.backend,
            point_cost=cost,
            provider=channel or (node.provider or ""),
            backend_model=vendor_model or (node.backend_model or ""),
            backend_models=dict(node.backend_models or {}),
        )
    return _RecordNode(
        name=model.name,
        backend=channel.split(":", 1)[0] if channel else "",
        point_cost=cost,
        provider=channel,
        backend_model=vendor_model,
    )


async def _save_output(output: NodeOutput, task_type_str: str) -> None:
    """产物落盘(复用 executor 异步落盘,失败仅告警;不堵事件循环)。"""
    from ...utils.core.executor import _save_output as legacy_save

    try:
        saved_path = await legacy_save(output, task_type_str)
        output.metadata["saved_path"] = str(saved_path)
    except Exception as e:  # noqa: BLE001 — 落盘失败不影响返回
        logger.warning(f"[Telemetry] 保存生成结果失败(不影响返回): {e}")


async def begin_dispatch(
    *,
    request: GenerationRequest,
    model: "AIGCGenerationBase",
    ctx: "DispatchContext",
    point_cost: int,
    request_body: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """预扣成功后立刻写入 running 行;失败返回 None。"""
    try:
        from ...utils.database.statistics import begin_task

        return await begin_task(
            request=request,
            request_body=request_body,
            node=_node_view(model, None, point_cost),
            bot_id=ctx.billing.bot_id,
            group_id=ctx.group_id,
            trace_id=request.trace_id or ctx.trace_id,
            entry_point=ctx.billing.entry_point,
            point_cost=point_cost,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Telemetry] begin_dispatch 失败(已忽略): {e}")
        return None


async def record_dispatch(
    *,
    request: GenerationRequest,
    result: Optional[GenerationResult],
    output: Optional[NodeOutput],
    model: "AIGCGenerationBase",
    status: str,
    elapsed_ms: int,
    error: Optional[str],
    ctx: "DispatchContext",
    point_cost: Optional[int] = None,
    request_body: Optional[dict[str, Any]] = None,
    record_id: Optional[int] = None,
) -> None:
    """终态统计落库(内部兜底,永不抛出)

    有 record_id 时 UPDATE 对应 running 行;否则 INSERT 终态(兼容 begin 失败)。
    失败路径位于 refund 之前 —— 不得抛出以免跳过退款。
    """
    try:
        if output is not None and status == "ok":
            await _save_output(output, request.task_type.value)

        from ...utils.database.statistics import record_task

        key_prefix = str(output.metadata.get("key_prefix", "")) if output is not None else ""

        await record_task(
            request=request,
            request_body=request_body,
            result=result,
            node=_node_view(model, output, point_cost),
            status=status,
            elapsed_ms=elapsed_ms,
            error=error,
            bot_id=ctx.billing.bot_id,
            group_id=ctx.group_id,
            trace_id=request.trace_id or ctx.trace_id,
            entry_point=ctx.billing.entry_point,
            backend_key_prefix=key_prefix,
            record_id=record_id,
            point_cost=point_cost,
        )
    except Exception as e:  # noqa: BLE001 — 统计失败不能影响主流程(尤其是退款)
        logger.warning(f"[Telemetry] record_dispatch 失败(已忽略): {e}")


__all__ = ["begin_dispatch", "record_dispatch"]
