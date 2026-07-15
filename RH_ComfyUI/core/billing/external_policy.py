"""ExternalPrepaidPolicy — 调用方已扣费(外部插件/外部记账系统 场景)

引擎侧只记账不扣费:外部插件的扣费/退款仍由其 generate_api 自己做,
本策略保证统计表的 point_cost 仍然写入正确金额。
"""

from __future__ import annotations

from .policy import BillingPolicy, BillingContext, BillingReservation


class ExternalPrepaidPolicy(BillingPolicy):
    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        return BillingReservation(cost=cost, context=ctx)

    async def refund(self, reservation: BillingReservation) -> None:
        reservation.refunded = True


__all__ = ["ExternalPrepaidPolicy"]
