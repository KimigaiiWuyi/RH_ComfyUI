"""生成引擎错误族 — dispatcher 与入口层按类型分流处理

分流规则(dispatcher 侧):
- ValidationError    → 不扣费不重试,message 直接给用户
- ChannelError       → retryable 决定通道切换;全部失败 → AllChannelsFailedError
- BillingDeniedError → 积分不足等,message 直接给用户
- 其余 Exception     → 记录 + 退款 + 包装为通用失败文案
"""

from __future__ import annotations

from typing import Optional


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
    """通道执行失败;retryable=True 时调度切换下一通道

    transient=True 表示"瞬时性失败"(如上游 429 限流 / 503 过载):通道本身健康,
    原通道指数退避排队(默认最长 1 小时)后大概率成功。排队超时才记失败并切换通道;
    该标注仅在 retryable=True 时有意义。
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        transient: bool = False,
        channel: str = "",
        code: str = "",
        user_message: Optional[str] = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.retryable = retryable
        self.transient = transient
        self.channel = channel
        self.code = code


class AllChannelsFailedError(GenerationError):
    def __init__(self, message: str, *, cause: Optional[Exception] = None) -> None:
        # 保留最后一个通道的干净用户文案(如供应商 failReason),
        # 否则调用方只会看到 "所有通道均失败" 这类无信息量的兜底文案。
        cause_user_message = getattr(cause, "user_message", None) if cause is not None else None
        super().__init__(message, user_message=cause_user_message)
        self.cause = cause


class BillingDeniedError(GenerationError):
    """计费拒绝(积分不足 / 账户异常)"""


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
