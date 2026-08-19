"""BillingPolicy — 计费策略接口(统一拦截,策略可插拔)

内置/可注册策略:
- PointsBillingPolicy   : RHBind 积分(bot 命令 / AI Agent 入口默认)
- ExternalPrepaidPolicy : 调用方已在外部记账(外部插件 走 外部记账系统),
                          引擎侧只记账不扣费
- 生态自定义            : 外部插件实现本 ABC 后在自己的入口注入
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
    external_ref: str = ""  # 外部记账凭据(如 外部 job_id)


@dataclass
class BillingReservation:
    """预扣凭据;commit/refund/settle 的幂等句柄"""

    cost: int
    context: BillingContext
    committed: bool = False
    refunded: bool = False
    settled_cost: int | None = None


class BillingPolicy(ABC):
    @abstractmethod
    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        """预扣;余额不足抛 BillingDeniedError(message 面向用户)"""

    @abstractmethod
    async def refund(self, reservation: BillingReservation) -> None:
        """失败退款;必须幂等(reservation.refunded 已 True 时直接返回)"""

    async def commit(self, reservation: BillingReservation) -> None:
        """成功确认;默认 no-op(预扣即终扣的策略无需实现)

        新代码请走 ``settle``:成功后若有供应商实扣,按差额对齐,禁止再扣一遍实际额。
        """
        reservation.committed = True
        if reservation.settled_cost is None:
            reservation.settled_cost = reservation.cost

    async def settle(self, reservation: BillingReservation, actual: int | None = None) -> int:
        """成功后按实际用量对齐预扣,只做差额,禁止按 actual 再全额扣一次。

        ``actual is None`` 或 ``<= 0``:预扣即终扣(与旧 commit 等价)。
        返回最终计入的积分数。已 commit/refund 时幂等返回已记账金额。
        默认实现只改 reservation 记账,不碰钱包(ExternalPrepaid 等宿主自管钱包)。
        """
        if reservation.refunded:
            return 0
        if reservation.committed:
            if reservation.settled_cost is not None:
                return reservation.settled_cost
            return reservation.cost
        if actual is None:
            final = reservation.cost
        else:
            try:
                n = int(actual)
            except (TypeError, ValueError):
                n = 0
            final = reservation.cost if n <= 0 else n
        reservation.settled_cost = final
        await self.commit(reservation)
        return final

    async def post_refund(self, reservation: BillingReservation, *, model_name: str) -> None:
        """退款后的记账钩子(如把统计表最近失败行标记 refunded);默认 no-op"""


__all__ = ["BillingContext", "BillingReservation", "BillingPolicy"]
