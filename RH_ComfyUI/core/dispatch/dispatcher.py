"""dispatch() — 唯一执行路径(三大入口都从这里进)

顺序(每一步的失败语义都已定义):
  1. route()           失败 → ModelUnavailableError(不扣费)
  2. validate 前置     失败 → ValidationError(不扣费)★ 校验先于扣费
  3. policy.reserve()  失败 → BillingDeniedError
  4. 并发闸 + model.run()
       成功 → policy.commit() → record_dispatch(status=ok)
       失败 → record_dispatch(status=failed) → policy.refund() → 原样抛出
"""

from __future__ import annotations

import time
from typing import Optional

from gsuid_core.logger import logger

from .context import DispatchContext
from .concurrency import generation_slot
from ..schema.types import NodeOutput
from ..schema.request import GenerationResult, GenerationRequest
from ..telemetry.recorder import record_dispatch


async def dispatch(request: GenerationRequest, ctx: DispatchContext) -> GenerationResult:
    from ..routing.router import route

    # 1. 路由(内部含 supports() 匹配与可用性过滤)
    model = await route(request)

    # 2. 校验先于扣费:参数错误不应该产生任何扣费/退款流水
    model.validate(request)

    # 3. 计费预扣
    reservation = await ctx.policy.reserve(ctx.billing, model.point_cost)
    if ctx.on_model_selected is not None:
        await ctx.on_model_selected(model, model.point_cost)

    # 4. 执行
    request.user_id = request.user_id or ctx.billing.user_id
    request.trace_id = request.trace_id or ctx.trace_id
    start = time.monotonic()
    output: Optional[NodeOutput] = None
    result: Optional[GenerationResult] = None
    try:
        async with generation_slot(model):
            logger.info(
                f"[dispatch] 执行生成: task={request.task_type.value}, "
                f"model={model.name}, entry={ctx.billing.entry_point}"
            )
            output = await model.run(request, on_progress=ctx.on_progress)
        result = GenerationResult.from_node_output(output)
        result.model_used = model.display_name
        result.pipeline_used = model.name
        result.cost_points = model.point_cost
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
        )
        return result
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        # 先落统计(status=failed),再退款并标记 refunded,顺序保证标记能找到记录
        await record_dispatch(
            request=request,
            result=None,
            output=output,
            model=model,
            status="failed",
            elapsed_ms=elapsed_ms,
            error=repr(e),
            ctx=ctx,
        )
        await ctx.policy.refund(reservation)
        if reservation.refunded:
            await ctx.policy.post_refund(reservation, model_name=model.name)
        logger.exception(f"[dispatch] 模型 {model.name} 执行失败")
        raise


__all__ = ["dispatch"]
