"""RHBind 积分策略 — 平移 utils/points.py + rh_generate 退款逻辑"""

from __future__ import annotations

from gsuid_core.logger import logger

from .policy import BillingPolicy, BillingContext, BillingReservation
from ..base.errors import BillingDeniedError


class PointsBillingPolicy(BillingPolicy):
    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        from ...utils.database.models import RHBind

        ok = await RHBind.deduct_point(ctx.user_id, ctx.bot_id, cost)
        if not ok:
            current = await RHBind.get_point(ctx.user_id, ctx.bot_id)
            raise BillingDeniedError(f"积分不足:本次需要 {cost} 积分,当前剩余 {current} 积分")
        return BillingReservation(cost=cost, context=ctx)

    async def refund(self, reservation: BillingReservation) -> None:
        if reservation.refunded or reservation.cost <= 0:
            return
        from ...utils.database.models import RHBind

        await RHBind.add_point(
            reservation.context.user_id,
            reservation.context.bot_id,
            reservation.cost,
        )
        reservation.refunded = True

    async def post_refund(self, reservation: BillingReservation, *, model_name: str) -> None:
        """把统计表最近一条失败记录标记为已退款(与旧 _do_generate 行为一致)"""
        from ...utils.database.models import RHComfyuiTaskRecord

        try:
            await RHComfyuiTaskRecord.mark_last_failed_refunded(
                user_id=reservation.context.user_id,
                bot_id=reservation.context.bot_id,
                task_name=model_name,
            )
        except Exception as e:  # noqa: BLE001 — 标记失败不影响主流程
            logger.warning(f"[Billing] mark refunded 失败(已忽略): {e}")


__all__ = ["PointsBillingPolicy"]
