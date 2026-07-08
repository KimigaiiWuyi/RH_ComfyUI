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
    """通道执行失败;retryable=True 时调度切换下一通道"""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        channel: str = "",
        code: str = "",
        user_message: Optional[str] = None,
    ) -> None:
        super().__init__(message, user_message=user_message)
        self.retryable = retryable
        self.channel = channel
        self.code = code


class AllChannelsFailedError(GenerationError):
    def __init__(self, message: str, *, cause: Optional[Exception] = None) -> None:
        # 保留最后一个通道的干净用户文案(如供应商 failReason),
        # 否则前端只会看到 "所有通道均失败" 这类无信息量的兜底文案。
        cause_user_message = getattr(cause, "user_message", None) if cause is not None else None
        super().__init__(message, user_message=cause_user_message)
        self.cause = cause


class BillingDeniedError(GenerationError):
    """计费拒绝(积分不足 / 账户异常)"""


__all__ = [
    "GenerationError",
    "ValidationError",
    "ModelUnavailableError",
    "ChannelError",
    "AllChannelsFailedError",
    "BillingDeniedError",
]
