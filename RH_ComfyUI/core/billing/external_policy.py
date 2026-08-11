"""ExternalPrepaidPolicy — 调用方已扣费(外部插件/外部记账系统 场景)

引擎侧只记账不扣费:外部插件的扣费/退款仍由其 generate_api 自己做,
本策略保证统计表的 point_cost 仍然写入正确金额。

取消约定(防双退):
1. 宿主先标自身任务终态并**自行退积分一次**
2. 再调 ``cancel_generation`` → dispatch CancelledError 会调用本 refund
3. 本策略**不**操作 RHBind,只把 reservation.refunded 置 True 以幂等
4. resume_poll 对 entry_point=http 同样不退 RHBind(见 resume._finalize_record)
"""

from __future__ import annotations

from .policy import BillingPolicy, BillingContext, BillingReservation


class ExternalPrepaidPolicy(BillingPolicy):
    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        return BillingReservation(cost=cost, context=ctx)

    async def refund(self, reservation: BillingReservation) -> None:
        # 宿主钱包自管:禁止引擎二次退 RHBind;仅幂等标记
        if reservation.refunded:
            return
        reservation.refunded = True


__all__ = ["ExternalPrepaidPolicy"]
