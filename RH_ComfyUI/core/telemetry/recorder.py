"""record_dispatch — 调度器侧的统计/落盘封装

职责(全部失败不影响主流程):
1. 产物落盘 OUTPUT_PATH(自旧 executor._save_output 平移,职责归遥测侧)
2. record_task() 落库,补 entry_point / channel 两个维度
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
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


def _node_view(model: "AIGCGenerationBase", output: Optional[NodeOutput]) -> _RecordNode:
    """把模型实例(可能是 YAML 桥接模型)投影为统计所需的节点视图"""
    node = model.node
    channel = ""
    vendor_model = ""
    if output is not None:
        channel = str(output.metadata.get("channel", ""))
        vendor_model = str(output.metadata.get("vendor_model", ""))
    if node is not None:
        return _RecordNode(
            name=node.name,
            backend=node.backend,
            point_cost=model.point_cost,
            provider=channel or (node.provider or ""),
            backend_model=vendor_model or (node.backend_model or ""),
            backend_models=dict(node.backend_models or {}),
        )
    return _RecordNode(
        name=model.name,
        backend=channel.split(":", 1)[0] if channel else "",
        point_cost=model.point_cost,
        provider=channel,
        backend_model=vendor_model,
    )


def _save_output(output: NodeOutput, task_type_str: str) -> None:
    """产物落盘(复用旧 executor 的实现,失败仅告警)"""
    from ...utils.core.executor import _save_output as legacy_save

    try:
        saved_path = legacy_save(output, task_type_str)
        output.metadata["saved_path"] = str(saved_path)
    except Exception as e:  # noqa: BLE001 — 落盘失败不影响返回
        logger.warning(f"[Telemetry] 保存生成结果失败(不影响返回): {e}")


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
) -> None:
    """统计落库(内部兜底,永不抛出)

    调用点在 dispatcher 的失败路径里位于 refund 之前 —— 这里一旦抛出,
    退款就会被跳过,所以整体 try/except 兜底(record_task 内部另有一层)。
    """
    try:
        if output is not None and status == "ok":
            _save_output(output, request.task_type.value)

        from ...utils.database.statistics import record_task

        key_prefix = str(output.metadata.get("key_prefix", "")) if output is not None else ""

        await record_task(
            request=request,
            result=result,
            node=_node_view(model, output),  # type: ignore[arg-type]  # duck-typed NodeDef 视图
            status=status,
            elapsed_ms=elapsed_ms,
            error=error,
            bot_id=ctx.billing.bot_id,
            group_id=ctx.group_id,
            trace_id=request.trace_id or ctx.trace_id,
            entry_point=ctx.billing.entry_point,
            backend_key_prefix=key_prefix,
        )
    except Exception as e:  # noqa: BLE001 — 统计失败不能影响主流程(尤其是退款)
        logger.warning(f"[Telemetry] record_dispatch 失败(已忽略): {e}")


__all__ = ["record_dispatch"]
