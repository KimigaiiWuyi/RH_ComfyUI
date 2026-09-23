"""dispatch() — 唯一执行路径(三大入口都从这里进)

顺序(每一步的失败语义都已定义):
  1. route()           失败 → ModelUnavailableError(不扣费)
  2. validate + ensure_channel_pin  失败 → ValidationError(不扣费)★ 校验先于扣费
  3. policy.reserve(model.estimate_cost(request))  失败 → BillingDeniedError
  3.5 begin_dispatch() 插入 RHComfyuiTaskRecord status=running(可查进行中)
      宿主要求消费行时,写入失败则在 model.run 之前中止,不向厂商提交
      同一 trace 仍在本进程执行则不再提交;已有厂商任务号则只轮询
  4. model.run() 内自带两层并发闸(供应商全局闸 + (model,channel) 闸),
       整体受超时预算约束(Dispatch_Timeout,0=不限)
       成功 → policy.settle(model.settle_cost(usage)) → record_dispatch(status=ok)
              settle 只做与预扣的差额(补扣/退差),禁止按实际再全额扣一次
       失败/超时 → record_dispatch(status=failed) → policy.refund() → 原样抛出
       厂商已接单但任务号没落上 → 补写成功则继续轮询;补写失败则尝试上游取消,
            消费行保持 running,不退款
       取消/中断(BaseException,如 CancelledError / DryRunInterrupt)
            → record_dispatch(status=cancelled|failed) → 退款 → 原样抛出
"""

from __future__ import annotations

import time
import asyncio
from typing import TYPE_CHECKING, Optional

from gsuid_core.logger import logger

from .context import DispatchContext
from .vendor_gate import is_task_record_required
from ..base.errors import GenerationError, VendorTaskNotDurable, GenerationAlreadyRunning, describe_exception
from .active_tasks import (
    REMOTE_CANCEL_TIMEOUT_S,
    ActiveGeneration,
    cancel_generation,
    _persist_vendor_task_id,
    get_active_task_registry,
)
from ..schema.types import NodeOutput
from ..billing.settle import invoke_settle_cost
from ..schema.request import GenerationResult, GenerationRequest
from ..telemetry.recorder import begin_dispatch, record_dispatch
from ...utils.core.safe_json import mask_body

if TYPE_CHECKING:
    from ...utils.database.statistics import BeganTask


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


def _coerce_began(value: object) -> "BeganTask | None":
    from ...utils.database.statistics import BeganTask

    if isinstance(value, BeganTask):
        return value if value.record_id > 0 else None
    if isinstance(value, int) and value > 0:
        return BeganTask(record_id=value)
    return None


def _keep_vendor_running(exc: BaseException) -> bool:
    """厂商任务还不能判定终态时，不要标失败、不要退款。"""
    from .resume import ResumeFailedError, ResumeNotSupportedError

    if isinstance(exc, ResumeNotSupportedError):
        return True
    return isinstance(exc, ResumeFailedError) and not exc.definitive


def _output_from_resume(resumed: object, *, fallback_kind: str) -> NodeOutput:
    from ...api import GenerationResult as ApiGenerationResult

    if not isinstance(resumed, ApiGenerationResult):
        raise GenerationError("resume_poll 未返回生成结果")
    data = resumed.data if isinstance(resumed.data, bytes) else b""
    raw = resumed.raw if isinstance(resumed.raw, dict) else {}
    usage = resumed.usage if isinstance(resumed.usage, dict) else {}
    outputs = resumed.outputs if isinstance(resumed.outputs, dict) else {}
    metadata = resumed.metadata if isinstance(resumed.metadata, dict) else {}
    return NodeOutput(
        output_type=resumed.kind or fallback_kind,
        data=data,
        mime_type=resumed.mime_type or "application/octet-stream",
        outputs=dict(outputs),
        usage=dict(usage),
        raw=dict(raw),
        metadata=dict(metadata),
    )


async def _poll_saved_vendor_task(
    *,
    model: object,
    request: GenerationRequest,
    ctx: DispatchContext,
    record_id: int,
    vendor_task_id: str,
    channel: str,
) -> NodeOutput:
    from .resume import resume_poll
    from ..base.generation import AIGCGenerationBase

    if not isinstance(model, AIGCGenerationBase):
        raise GenerationError("resume_poll 缺少模型")
    node = model.node
    backend = node.backend if node is not None and node.backend else ""
    resumed = await resume_poll(
        model=model.name,
        vendor_task_id=vendor_task_id,
        channel=channel,
        backend=backend,
        kind=request.task_type.value,
        trace_id=request.trace_id or ctx.trace_id,
        record_id=record_id,
        on_progress=ctx.on_progress,
        update_record=False,
    )
    return _output_from_resume(resumed, fallback_kind=request.task_type.value)


async def _retry_durable_poll(
    exc: VendorTaskNotDurable,
    *,
    model: object,
    request: GenerationRequest,
    ctx: DispatchContext,
) -> NodeOutput | None:
    if exc.record_id <= 0 or not exc.vendor_task_id:
        return None
    try:
        saved = await _persist_vendor_task_id(
            record_id=exc.record_id,
            vendor_task_id=exc.vendor_task_id,
            channel_name=exc.channel,
        )
    except Exception as persist_exc:
        logger.exception(
            f"[dispatch] 补写厂商任务号失败 record_id={exc.record_id} "
            f"vendor_task_id={exc.vendor_task_id}: {persist_exc}"
        )
        return None
    if not saved:
        return None
    return await _poll_saved_vendor_task(
        model=model,
        request=request,
        ctx=ctx,
        record_id=exc.record_id,
        vendor_task_id=exc.vendor_task_id,
        channel=exc.channel,
    )


async def _cancel_bound_remote(ag: ActiveGeneration | None) -> None:
    if ag is None or ag.cancel_remote is None or ag.remote_cancel_attempted:
        return
    ag.remote_cancel_attempted = True
    try:
        await asyncio.wait_for(ag.cancel_remote(), timeout=REMOTE_CANCEL_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — 上游取消失败仍要留下 running 行
        logger.warning(f"[dispatch] 厂商已接单但任务号未落库,上游取消失败 vendor_task_id={ag.vendor_task_id}: {exc}")


async def dispatch(request: GenerationRequest, ctx: DispatchContext) -> GenerationResult:
    from ..routing.router import route

    # 在校验/normalize/上下文回填之前冻结输入,保证统计表记录调用方原始请求。
    request_body = mask_body(request)
    # 成片在校验前钉扎样片通道和计价时长,命令与 submit 走同一处
    caller = (request.user_id or ctx.billing.user_id or "").strip()
    from ...utils.backends.seedance.draft import prepare_seedance25_final_request

    await prepare_seedance25_final_request(request, user_id=caller)

    # 1. 路由(内部含 supports() 匹配与可用性过滤)
    model = await route(request)

    # 2. 校验先于扣费:参数错误不应该产生任何扣费/退款流水
    model.validate(request)
    await model.ensure_channel_pin(request)

    # 3. 计费预扣:金额由动态计费钩子决定(默认 = 静态 point_cost)
    cost = model.estimate_cost(request)
    reservation = await ctx.policy.reserve(ctx.billing, cost)
    if ctx.on_model_selected is not None:
        await ctx.on_model_selected(model, cost)

    # 4. 执行
    request.user_id = request.user_id or ctx.billing.user_id
    request.trace_id = request.trace_id or ctx.trace_id
    active_reg = get_active_task_registry()
    trace = (request.trace_id or ctx.trace_id or "").strip()
    record_id: Optional[int] = None
    active_handle: ActiveGeneration | None = None
    # 清空上游 wire 快照;backend 在 POST 前 set_wire_audit,record 时取最终 body
    from ..telemetry.wire_capture import clear_wire_audit

    clear_wire_audit()
    start = time.monotonic()
    output: Optional[NodeOutput] = None

    async def _commit(done: NodeOutput) -> GenerationResult:
        done_result = GenerationResult.from_node_output(done)
        done_result.model_used = model.display_name
        done_result.pipeline_used = model.name
        done_result.outputs = done.outputs
        done_result.usage = done.usage
        done_result.raw = done.raw
        settle_usage = dict(done.usage or {})
        if isinstance(done.raw, dict) and done.raw:
            settle_usage.setdefault("raw_task", done.raw)
        actual = invoke_settle_cost(model, request, settle_usage)
        final_cost = await ctx.policy.settle(reservation, actual)
        done_result.cost_points = final_cost
        elapsed_ms = int((time.monotonic() - start) * 1000)
        done_result.metadata.setdefault("elapsed_ms", elapsed_ms)
        if actual is not None and actual != cost:
            done_result.metadata["prepaid_points"] = cost
            done_result.metadata["settled_points"] = final_cost
        await record_dispatch(
            request=request,
            request_body=request_body,
            result=done_result,
            output=done,
            model=model,
            status="ok",
            elapsed_ms=elapsed_ms,
            error=None,
            ctx=ctx,
            point_cost=final_cost,
            record_id=record_id,
        )
        return done_result

    async def _run_slotted() -> NodeOutput:
        # 并发闸全部在 model.run() 内部(channel_slot + channel_slot_for_model 两层);
        # 本层仅做超时预算 + 日志。详见 core/dispatch/concurrency.py。
        logger.info(
            f"[dispatch] 执行生成: task={request.task_type.value}, model={model.name}, entry={ctx.billing.entry_point}"
        )
        return await model.run(request, on_progress=ctx.on_progress)

    try:
        live = active_reg.get_by_trace(trace) if trace else None
        if live is not None and live.task is not None and not live.task.done():
            raise GenerationAlreadyRunning(f"trace={trace} 仍在执行 model={live.model_name}")
        # 预扣成功立刻写 running 行,消费列表可见进行中任务
        began = _coerce_began(
            await begin_dispatch(
                request=request,
                request_body=request_body,
                model=model,
                ctx=ctx,
                point_cost=cost,
            )
        )
        if began is not None:
            record_id = began.record_id
        # 消费行是召回的唯一锚点。写不进去就停在厂商请求之前。
        if is_task_record_required() and not record_id:
            raise GenerationError(
                "RH 消费行未写入，已中止，未向厂商提交",
                user_message="任务记录未能保存，未向厂商提交，请稍后重试。",
            )
        # 登记进行中任务;supports_cancel=False(如 rh_app)时 cancel_generation 会拒绝
        active_handle = await active_reg.register(
            model_name=model.name,
            trace_id=trace,
            record_id=record_id,
            user_id=request.user_id or ctx.billing.user_id or "",
            allow_cancel=bool(model.supports_cancel),
        )
        if began is not None and began.vendor_task_id:
            output = await _poll_saved_vendor_task(
                model=model,
                request=request,
                ctx=ctx,
                record_id=began.record_id,
                vendor_task_id=began.vendor_task_id,
                channel=began.vendor_channel,
            )
        else:
            timeout_s = _resolve_timeout()
            if timeout_s > 0:
                try:
                    output = await asyncio.wait_for(_run_slotted(), timeout=timeout_s)
                except asyncio.TimeoutError as te:
                    # 超时按普通失败处理(落统计 status=failed + 退款),不能把
                    # 内部的 CancelledError 语义透传出去(那会被记成 cancelled)
                    raise GenerationError(
                        f"{model.name} 执行超过超时预算 {timeout_s:.0f}s",
                        user_message="生成超时，请查询原任务状态及积分回执，不要重复提交。",
                    ) from te
            else:
                output = await _run_slotted()
        if output is None:
            raise GenerationError(f"{model.name} 未返回生成结果")
        return await _commit(output)
    except BaseException as e:
        fault: BaseException = e
        if isinstance(e, GenerationAlreadyRunning):
            await ctx.policy.refund(reservation)
            logger.warning(f"[dispatch] 模型 {model.name} 仍在执行,未再次提交")
            raise
        if isinstance(e, VendorTaskNotDurable):
            try:
                polled = await _retry_durable_poll(e, model=model, request=request, ctx=ctx)
            except BaseException as poll_exc:
                if isinstance(poll_exc, VendorTaskNotDurable) or _keep_vendor_running(poll_exc):
                    await _cancel_bound_remote(active_handle)
                    raise
                fault = poll_exc
            else:
                if polled is None:
                    await _cancel_bound_remote(active_handle)
                    logger.error(
                        f"[dispatch] 厂商已接单但任务号未落库,保持 running 且不退款 model={model.name} "
                        f"vendor_task_id={e.vendor_task_id}"
                    )
                    raise
                if record_id is None and e.record_id > 0:
                    record_id = e.record_id
                return await _commit(polled)
        if _keep_vendor_running(fault):
            logger.error(
                f"[dispatch] 厂商任务未到可退款终态,保持 running model={model.name}: {describe_exception(fault)}"
            )
            raise
        # 必须接 BaseException:任务取消(CancelledError)与后端的
        # Dry-Run 中断信号(DryRunInterrupt,继承 BaseException)同样意味着
        # "预扣了积分但没有产物",漏接会导致积分被吞、统计缺行。
        from .resume import ResumeCancelledError

        elapsed_ms = int((time.monotonic() - start) * 1000)
        if isinstance(fault, (asyncio.CancelledError, ResumeCancelledError)):
            status = "cancelled"
        else:
            status = "failed"
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
            error=describe_exception(fault),
            ctx=ctx,
            point_cost=cost,
            record_id=record_id,
        )
        await ctx.policy.refund(reservation)
        if reservation.refunded:
            await ctx.policy.post_refund(reservation, model_name=model.name)
        if isinstance(fault, GenerationError):
            # 已知域错误(通道失败/超时/校验/计费):根因链 describe_exception 一行
            # 说清;logger.exception 会把 __cause__ 链上的 httpx/httpcore 内部帧
            # 全部展开(一次网络失败 100+ 行 rich traceback),纯刷屏无增量信息。
            logger.error(f"[dispatch] 模型 {model.name} 执行失败: {describe_exception(fault)}")
        elif isinstance(fault, Exception):
            # 非预期异常(真 bug)才需要完整 traceback 定位
            logger.exception(f"[dispatch] 模型 {model.name} 执行失败")
        else:
            logger.warning(f"[dispatch] 模型 {model.name} 执行中断({type(fault).__name__}),已退款")
        if fault is not e:
            raise fault
        raise
    finally:
        if active_handle is not None:
            await active_reg.unregister(active_handle)
        clear_wire_audit()


__all__ = ["dispatch", "cancel_generation"]
