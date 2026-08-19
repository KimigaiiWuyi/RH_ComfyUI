"""dispatch() — 唯一执行路径(三大入口都从这里进)

顺序(每一步的失败语义都已定义):
  1. route()           失败 → ModelUnavailableError(不扣费)
  2. validate 前置     失败 → ValidationError(不扣费)★ 校验先于扣费
  3. policy.reserve(model.estimate_cost(request))  失败 → BillingDeniedError
  3.5 begin_dispatch() 插入 RHComfyuiTaskRecord status=running(可查进行中)
  4. model.run() 内自带两层并发闸(供应商全局闸 + (model,channel) 闸),
       整体受超时预算约束(Dispatch_Timeout,0=不限)
       成功 → policy.settle(model.settle_cost(usage)) → record_dispatch(status=ok)
              settle 只做与预扣的差额(补扣/退差),禁止按实际再全额扣一次
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
from ..base.errors import GenerationError, describe_exception
from .active_tasks import cancel_generation, get_active_task_registry
from ..schema.types import NodeOutput
from ..billing.settle import invoke_settle_cost
from ..schema.request import GenerationResult, GenerationRequest
from ..telemetry.recorder import begin_dispatch, record_dispatch
from ...utils.core.safe_json import mask_body


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

    # 在校验/normalize/上下文回填之前冻结输入,保证统计表记录调用方原始请求。
    request_body = mask_body(request)

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
    # 预扣成功立刻写 running 行,消费列表可见进行中任务
    record_id = await begin_dispatch(
        request=request,
        request_body=request_body,
        model=model,
        ctx=ctx,
        point_cost=cost,
    )
    # 登记进行中任务;supports_cancel=False(如 rh_app)时 cancel_generation 会拒绝
    active_reg = get_active_task_registry()
    active_handle = await active_reg.register(
        model_name=model.name,
        trace_id=request.trace_id or ctx.trace_id or "",
        record_id=record_id,
        user_id=request.user_id or ctx.billing.user_id or "",
        allow_cancel=bool(model.supports_cancel),
    )
    # 清空上游 wire 快照;backend 在 POST 前 set_wire_audit,record 时取最终 body
    from ..telemetry.wire_capture import clear_wire_audit

    clear_wire_audit()
    start = time.monotonic()
    output: Optional[NodeOutput] = None
    result: Optional[GenerationResult] = None

    async def _run_slotted() -> NodeOutput:
        # 并发闸全部在 model.run() 内部(channel_slot + channel_slot_for_model 两层);
        # 本层仅做超时预算 + 日志。详见 core/dispatch/concurrency.py。
        logger.info(
            f"[dispatch] 执行生成: task={request.task_type.value}, model={model.name}, entry={ctx.billing.entry_point}"
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
        result.outputs = output.outputs
        result.usage = output.usage
        result.raw = output.raw
        settle_usage = dict(output.usage or {})
        if isinstance(output.raw, dict) and output.raw:
            settle_usage.setdefault("raw_task", output.raw)
        actual = invoke_settle_cost(model, request, settle_usage)
        final_cost = await ctx.policy.settle(reservation, actual)
        result.cost_points = final_cost
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.metadata.setdefault("elapsed_ms", elapsed_ms)
        if actual is not None and actual != cost:
            result.metadata["prepaid_points"] = cost
            result.metadata["settled_points"] = final_cost
        await record_dispatch(
            request=request,
            request_body=request_body,
            result=result,
            output=output,
            model=model,
            status="ok",
            elapsed_ms=elapsed_ms,
            error=None,
            ctx=ctx,
            point_cost=final_cost,
            record_id=record_id,
        )
        return result
    except BaseException as e:
        # 必须接 BaseException:任务取消(CancelledError)与后端的
        # Dry-Run 中断信号(DryRunInterrupt,继承 BaseException)同样意味着
        # "预扣了积分但没有产物",漏接会导致积分被吞、统计缺行。
        elapsed_ms = int((time.monotonic() - start) * 1000)
        status = "cancelled" if isinstance(e, asyncio.CancelledError) else "failed"
        # 先更新统计为终态,再退款并 mark refunded(需 status=failed 已落库)
        await record_dispatch(
            request=request,
            request_body=request_body,
            result=None,
            output=output,
            model=model,
            status=status,
            elapsed_ms=elapsed_ms,
            # 展开成因链:AllChannelsFailedError 的真实根因在 .cause 里
            error=describe_exception(e),
            ctx=ctx,
            point_cost=cost,
            record_id=record_id,
        )
        await ctx.policy.refund(reservation)
        if reservation.refunded:
            await ctx.policy.post_refund(reservation, model_name=model.name)
        if isinstance(e, GenerationError):
            # 已知域错误(通道失败/超时/校验/计费):根因链 describe_exception 一行
            # 说清;logger.exception 会把 __cause__ 链上的 httpx/httpcore 内部帧
            # 全部展开(一次网络失败 100+ 行 rich traceback),纯刷屏无增量信息。
            logger.error(f"[dispatch] 模型 {model.name} 执行失败: {describe_exception(e)}")
        elif isinstance(e, Exception):
            # 非预期异常(真 bug)才需要完整 traceback 定位
            logger.exception(f"[dispatch] 模型 {model.name} 执行失败")
        else:
            logger.warning(f"[dispatch] 模型 {model.name} 执行中断({type(e).__name__}),已退款")
        raise
    finally:
        await active_reg.unregister(active_handle)
        clear_wire_audit()


__all__ = ["dispatch", "cancel_generation"]
