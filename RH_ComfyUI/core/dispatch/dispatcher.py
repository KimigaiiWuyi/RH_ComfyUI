"""dispatch() — 唯一执行路径(三大入口都从这里进)

顺序(每一步的失败语义都已定义):
  1. route()           失败 → ModelUnavailableError(不扣费)
  2. validate 前置     失败 → ValidationError(不扣费)★ 校验先于扣费
  3. policy.reserve(model.estimate_cost(request))  失败 → BillingDeniedError
  4. 并发闸 + model.run(),整体受超时预算约束(Dispatch_Timeout,0=不限)
       成功 → policy.commit() → record_dispatch(status=ok)
       失败/超时 → record_dispatch(status=failed) → policy.refund() → 原样抛出
       取消/中断(BaseException,如 CancelledError / DryRunInterrupt)
            → record_dispatch(status=cancelled|failed) → 退款 → 原样抛出
"""

from __future__ import annotations

import time
import asyncio
from typing import Optional

from gsuid_core.logger import logger

from .context import DispatchContext
from .concurrency import generation_slot
from ..base.errors import GenerationError
from ..schema.types import NodeOutput
from ..schema.request import GenerationResult, GenerationRequest
from ..telemetry.recorder import record_dispatch


def _resolve_timeout() -> float:
    """单任务超时预算(秒);每次 dispatch 实时读配置(热更新)。0=不限制。

    预算覆盖"排队等并发闸 + 执行"全程:排队发生在扣费之后,没有预算时
    一个卡死的上游会占住全局并发闸,后续任务全部堵在闸外。
    """
    from ...rh_config.comfyui_config import PLUGIN_CONFIG

    raw = PLUGIN_CONFIG.get_config("Dispatch_Timeout").data
    if isinstance(raw, int) and raw > 0:
        return float(raw)
    return 0.0


async def dispatch(request: GenerationRequest, ctx: DispatchContext) -> GenerationResult:
    from ..routing.router import route

    # 1. 路由(内部含 supports() 匹配与可用性过滤)
    model = await route(request)

    # 2. 校验先于扣费:参数错误不应该产生任何扣费/退款流水
    model.validate(request)

    # 3. 计费预扣:金额由动态计费钩子决定(默认 = 静态 point_cost)
    cost = model.estimate_cost(request)
    reservation = await ctx.policy.reserve(ctx.billing, cost)
    if ctx.on_model_selected is not None:
        await ctx.on_model_selected(model, cost)

    # 4. 执行
    request.user_id = request.user_id or ctx.billing.user_id
    request.trace_id = request.trace_id or ctx.trace_id
    start = time.monotonic()
    output: Optional[NodeOutput] = None
    result: Optional[GenerationResult] = None

    async def _run_slotted() -> NodeOutput:
        async with generation_slot(model):
            logger.info(
                f"[dispatch] 执行生成: task={request.task_type.value}, "
                f"model={model.name}, entry={ctx.billing.entry_point}"
            )
            return await model.run(request, on_progress=ctx.on_progress)

    try:
        timeout_s = _resolve_timeout()
        if timeout_s > 0:
            try:
                output = await asyncio.wait_for(_run_slotted(), timeout=timeout_s)
            except asyncio.TimeoutError as te:
                # 超时按普通失败处理(落统计 status=failed + 退款),不能把
                # 内部的 CancelledError 语义透传出去(那会被记成 cancelled)
                raise GenerationError(
                    f"{model.name} 执行超过超时预算 {timeout_s:.0f}s",
                    user_message="生成超时,积分已退回,请稍后重试。",
                ) from te
        else:
            output = await _run_slotted()
        result = GenerationResult.from_node_output(output)
        result.model_used = model.display_name
        result.pipeline_used = model.name
        result.cost_points = cost
        result.outputs = output.outputs
        result.usage = output.usage
        result.raw = output.raw
        await ctx.policy.commit(reservation)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.metadata.setdefault("elapsed_ms", elapsed_ms)
        await record_dispatch(
            request=request,
            result=result,
            output=output,
            model=model,
            status="ok",
            elapsed_ms=elapsed_ms,
            error=None,
            ctx=ctx,
            point_cost=cost,
        )
        return result
    except BaseException as e:
        # 必须接 BaseException:任务取消(CancelledError)与后端的
        # Dry-Run 中断信号(DryRunInterrupt,继承 BaseException)同样意味着
        # "预扣了积分但没有产物",漏接会导致积分被吞、统计缺行。
        elapsed_ms = int((time.monotonic() - start) * 1000)
        status = "cancelled" if isinstance(e, asyncio.CancelledError) else "failed"
        # 先落统计(status=failed),再退款并标记 refunded,顺序保证标记能找到记录
        await record_dispatch(
            request=request,
            result=None,
            output=output,
            model=model,
            status=status,
            elapsed_ms=elapsed_ms,
            error=repr(e),
            ctx=ctx,
            point_cost=cost,
        )
        await ctx.policy.refund(reservation)
        if reservation.refunded:
            await ctx.policy.post_refund(reservation, model_name=model.name)
        if isinstance(e, Exception):
            logger.exception(f"[dispatch] 模型 {model.name} 执行失败")
        else:
            logger.warning(f"[dispatch] 模型 {model.name} 执行中断({type(e).__name__}),已退款")
        raise


__all__ = ["dispatch"]
