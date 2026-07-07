"""DispatchContext — 一次生成调用的完整上下文(入口层构造后传给 dispatch)"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Awaitable
from dataclasses import dataclass

from ..schema.types import ProgressCallback
from ..billing.policy import BillingPolicy, BillingContext

if TYPE_CHECKING:
    from ..base.generation import AIGCGenerationBase

# 模型选定回调:入口层可在扣费成功后立即向用户播报"使用模型 X,已扣 N 积分"
OnModelSelected = Callable[["AIGCGenerationBase", int], Awaitable[None]]


@dataclass
class DispatchContext:
    billing: BillingContext
    policy: BillingPolicy
    on_progress: Optional[ProgressCallback] = None
    on_model_selected: Optional[OnModelSelected] = None
    group_id: str = ""
    trace_id: str = ""


__all__ = ["DispatchContext", "OnModelSelected"]
