"""BillingPolicy — 计费策略接口(统一拦截,策略可插拔)

内置/可注册策略:
- PointsBillingPolicy   : RHBind 积分(bot 命令 / AI Agent 入口默认)
- ExternalPrepaidPolicy : 调用方已在外部记账(canvas_backend 走 account_system),
                          引擎侧只记账不扣费
- 生态自定义            : 另外的兼容插件生态实现本 ABC 后在自己的入口注入
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class BillingContext:
    """一次生成的计费上下文(由入口层构造)"""

    user_id: str
    bot_id: str = ""
    entry_point: str = "command"  # command / agent / http
    external_ref: str = ""  # 外部记账凭据(如 canvas job_id)


@dataclass
class BillingReservation:
    """预扣凭据;commit/refund 的幂等句柄"""

    cost: int
    context: BillingContext
    committed: bool = False
    refunded: bool = False


class BillingPolicy(ABC):
    @abstractmethod
    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        """预扣;余额不足抛 BillingDeniedError(message 面向用户)"""

    @abstractmethod
    async def refund(self, reservation: BillingReservation) -> None:
        """失败退款;必须幂等(reservation.refunded 已 True 时直接返回)"""

    async def commit(self, reservation: BillingReservation) -> None:
        """成功确认;默认 no-op(预扣即终扣的策略无需实现)"""
        reservation.committed = True

    async def post_refund(self, reservation: BillingReservation, *, model_name: str) -> None:
        """退款后的记账钩子(如把统计表最近失败行标记 refunded);默认 no-op"""


__all__ = ["BillingContext", "BillingReservation", "BillingPolicy"]
