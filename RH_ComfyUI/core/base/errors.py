"""生成引擎错误族 — dispatcher 与入口层按类型分流处理

分流规则(dispatcher 侧):
- ValidationError    → 不扣费不重试,message 直接给用户
- ChannelError       → retryable 决定通道切换;全部失败 → AllChannelsFailedError
- BillingDeniedError → 积分不足等,message 直接给用户
- 其余 Exception     → 记录 + 退款 + 包装为通用失败文案
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..channels.failure import Phase, Stage, FailureKind


class GenerationError(RuntimeError):
    """引擎错误基类;user_message 面向最终用户(默认同 message)"""

    def __init__(self, message: str, *, user_message: Optional[str] = None) -> None:
        super().__init__(message)
        self.user_message = user_message if user_message is not None else message


class ValidationError(GenerationError):
    """参数校验失败(用户可修正)"""


class ModelUnavailableError(GenerationError):
    """无可用模型(沿用旧 router 同名异常的语义)"""


class ChannelError(GenerationError):
    """通道执行失败;后端只上报事实(kind/phase),换源与熔断由 run() 唯一决策

    kind/phase 声明时(推荐):
    - retryable 推导 = kind != INVALID_REQUEST 且 (phase != ACCEPTED 或 kind == TASK_FAILED);
    - transient 推导 = retryable 且 (kind == RATE_LIMITED 或 (PROVIDER_FAULT 且 http_status==503));
    - 显式 retryable/transient 优先于推导(覆盖特殊站点,如某家把 402 当不可重试)。

    旧式构造(无 kind):从显式 retryable/transient 反推 resolved_kind,
    phase 保持 UNKNOWN(strict 下按 AMBIGUOUS 处理,不换源)。
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: Optional[bool] = None,
        transient: Optional[bool] = None,
        kind: "FailureKind | None" = None,
        phase: "Phase | None" = None,
        http_status: Optional[int] = None,
        channel: str = "",
        code: str = "",
        user_message: Optional[str] = None,
    ) -> None:
        from ..channels.failure import Phase as _Phase, FailureKind as _FK

        if phase is None:
            phase = _Phase.UNKNOWN
        super().__init__(message, user_message=user_message)
        self.kind = kind
        self.phase = phase
        self.http_status = http_status
        if kind is not None:
            derived_retryable = kind is not _FK.INVALID_REQUEST and (
                phase is not _Phase.ACCEPTED or kind is _FK.TASK_FAILED
            )
            derived_transient = derived_retryable and (
                kind is _FK.RATE_LIMITED or (kind is _FK.PROVIDER_FAULT and http_status == 503)
            )
            self.retryable = derived_retryable if retryable is None else bool(retryable)
            self.transient = derived_transient if transient is None else bool(transient)
        else:
            self.retryable = bool(retryable)
            self.transient = bool(transient)
        self.channel = channel
        self.code = code

    @property
    def resolved_kind(self) -> "FailureKind":
        """显式 kind,或旧式构造时按 retryable/transient 反推。"""
        from ..channels.failure import FailureKind

        if self.kind is not None:
            return self.kind
        if not self.retryable:
            return FailureKind.INVALID_REQUEST
        if self.transient:
            return FailureKind.RATE_LIMITED
        return FailureKind.PROVIDER_FAULT

    @classmethod
    def from_http(
        cls,
        status: int,
        *,
        stage: "Stage",
        channel: str,
        code: str = "HTTP_ERROR",
        message: str,
        user_message: Optional[str] = None,
        kind: "FailureKind | None" = None,
        retryable: Optional[bool] = None,
        transient: Optional[bool] = None,
    ) -> "ChannelError":
        """HTTP 状态 → ChannelError;kind 可显式覆盖(如网关信封的自定义语义)。"""
        from ..channels.failure import classify_http_status

        ck, phase = classify_http_status(status, stage=stage)
        return cls(
            message,
            kind=kind if kind is not None else ck,
            phase=phase,
            http_status=status,
            channel=channel,
            code=code,
            user_message=user_message,
            retryable=retryable,
            transient=transient,
        )

    @classmethod
    def from_transport(
        cls,
        exc: BaseException,
        *,
        stage: "Stage",
        channel: str,
        code: str = "PROVIDER_NETWORK_ERROR",
        message: Optional[str] = None,
        user_message: Optional[str] = None,
    ) -> "ChannelError":
        """传输异常 → ChannelError(kind=PROVIDER_FAULT,phase 由异常链与阶段推)。"""
        from ..channels.failure import classify_transport

        _kind, _phase = classify_transport(exc, stage=stage)
        return cls(
            message if message is not None else f"{type(exc).__name__}: {exc}",
            kind=_kind,
            phase=_phase,
            channel=channel,
            code=code,
            user_message=user_message,
        )


class AllChannelsFailedError(GenerationError):
    def __init__(
        self,
        message: str,
        *,
        cause: Optional[Exception] = None,
        skipped_notes: Optional[list[str]] = None,
    ) -> None:
        # 保留最后一个通道的干净用户文案(如供应商 failReason),
        # 并附带跳过通道的诊断信息,避免丢失关键凭证缺失提示。
        cause_user_message = getattr(cause, "user_message", None) if cause is not None else None
        if cause_user_message:
            suffix = f" (备用通道未尝试: {', '.join(skipped_notes)})" if skipped_notes else ""
            final_user_msg = f"{cause_user_message}{suffix}"
        else:
            final_user_msg = message
        super().__init__(message, user_message=final_user_msg)
        self.cause = cause


class BillingDeniedError(GenerationError):
    """计费拒绝(积分不足 / 账户异常)"""


class GenerationAlreadyRunning(GenerationError):
    """同一 trace 仍在本进程执行。不要再提交，也不要把原消费行打成失败。"""

    def __init__(self, message: str, *, user_message: Optional[str] = None) -> None:
        super().__init__(
            message,
            user_message=user_message if user_message is not None else "任务仍在执行，请勿重复生成。",
        )


class VendorTaskNotDurable(GenerationError):
    """厂商已返回任务号，但消费行没写上，不能当成可召回。"""

    def __init__(
        self,
        message: str,
        *,
        record_id: int,
        vendor_task_id: str,
        channel: str = "",
    ) -> None:
        super().__init__(
            message,
            user_message="上游已受理，任务号暂未保存，请勿重复生成。",
        )
        self.record_id = record_id
        self.vendor_task_id = vendor_task_id
        self.channel = channel


class DryRunInterrupt(BaseException):
    """全局 Dry-Run 中断信号。

    继承 BaseException,避免被 ``except Exception`` 当成通道失败去熔断/切换。
    dispatcher 按 BaseException 兜底:落统计(failed)+ 退款后原样抛出。
    """


def _next_cause(exc: BaseException) -> Optional[BaseException]:
    """成因链的下一环:引擎自持的 cause 优先,再退标准 __cause__ / __context__

    AllChannelsFailedError 的根因存在 .cause(raise 时没走 `from`,标准链为空),
    必须先取它;其余异常按 Python 语义走 __cause__(显式 from)或未被抑制的
    __context__(隐式链)。
    """
    if isinstance(exc, AllChannelsFailedError) and exc.cause is not None:
        return exc.cause
    if exc.__cause__ is not None:
        return exc.__cause__
    if not exc.__suppress_context__:
        return exc.__context__
    return None


def describe_exception(exc: BaseException, *, max_depth: int = 6) -> str:
    """把异常连同成因链展开成单行诊断串,供落库 error_message 定位根因。

    仅记 repr(顶层异常)会把 AllChannelsFailedError 的真实根因(通道错误 /
    厂商 failReason)丢在 .cause 里,导致每条失败记录都长一个样。本函数沿
    成因链逐环拼接,ChannelError 额外附 channel/code 定位到具体供应商;相邻
    两环文案完全相同(如 ChannelError 直接透传下层 str(exc))时跳过纯重复。
    """
    parts: list[str] = []
    seen: set[int] = set()
    last_msg: Optional[str] = None
    cur: Optional[BaseException] = exc
    while cur is not None and len(parts) < max_depth and id(cur) not in seen:
        seen.add(id(cur))
        msg = str(cur)
        tags = ""
        if isinstance(cur, ChannelError):
            fields: list[str] = []
            if cur.channel:
                fields.append(f"channel={cur.channel}")
            if cur.code:
                fields.append(f"code={cur.code}")
            if fields:
                tags = f"[{', '.join(fields)}]"
        if msg == last_msg and not tags:
            # 下层只是原样重复上层文案且无额外定位信息,跳过这一环
            cur = _next_cause(cur)
            continue
        label = f"{type(cur).__name__}{tags}"
        parts.append(f"{label}: {msg}" if msg else label)
        last_msg = msg
        cur = _next_cause(cur)
    return " <- ".join(parts)


__all__ = [
    "GenerationError",
    "ValidationError",
    "ModelUnavailableError",
    "ChannelError",
    "AllChannelsFailedError",
    "BillingDeniedError",
    "describe_exception",
]
