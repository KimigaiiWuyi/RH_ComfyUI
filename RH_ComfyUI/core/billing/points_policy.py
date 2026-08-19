"""RHBind 三重余额策略 — bot / agent 入口默认。"""

from __future__ import annotations

from gsuid_core.logger import logger

from .policy import BillingPolicy, BillingContext, BillingReservation
from ..base.errors import BillingDeniedError


class PointsBillingPolicy(BillingPolicy):
    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        from ...utils.database.models import RHBind

        # vip_tier=None → 使用 RHBind 行上已存档位(与 bot_id 无关)
        ok, detail = await RHBind.deduct_triple(
            ctx.user_id,
            ctx.bot_id,
            cost,
            vip_tier=None,
        )
        if not ok:
            reason = detail.get("reason") or f"积分不足:本次需要 {cost} 积分"
            avail = detail.get("available", 0)
            raise BillingDeniedError(f"{reason}(需要 {cost},可用 {avail})")
        return BillingReservation(cost=cost, context=ctx)

    async def refund(self, reservation: BillingReservation) -> None:
        if reservation.refunded or reservation.cost <= 0:
            return
        from ...utils.database.models import RHBind

        await RHBind.add_triple(
            reservation.context.user_id,
            reservation.context.bot_id,
            reservation.cost,
            vip_tier=None,
            cap_to_tier=True,
        )
        reservation.refunded = True

    async def settle(self, reservation: BillingReservation, actual: int | None = None) -> int:
        """预扣后按实际用量补扣或退差;补扣失败则维持预扣(生成已成功,不打翻主流程)。"""
        if reservation.refunded:
            return 0
        if reservation.committed:
            if reservation.settled_cost is not None:
                return reservation.settled_cost
            return reservation.cost

        prepaid = reservation.cost
        if actual is None:
            final = prepaid
        else:
            try:
                n = int(actual)
            except (TypeError, ValueError):
                n = 0
            final = prepaid if n <= 0 else n

        delta = final - prepaid
        if delta > 0:
            from ...utils.database.models import RHBind

            ok, detail = await RHBind.deduct_triple(
                reservation.context.user_id,
                reservation.context.bot_id,
                delta,
                vip_tier=None,
            )
            if not ok:
                reason = (detail or {}).get("reason") or "积分不足"
                logger.warning(
                    f"[Billing] 后结算补扣失败,维持预扣 user={reservation.context.user_id} "
                    f"prepaid={prepaid} actual={final} extra={delta} reason={reason}"
                )
                final = prepaid
        elif delta < 0:
            from ...utils.database.models import RHBind

            await RHBind.add_triple(
                reservation.context.user_id,
                reservation.context.bot_id,
                -delta,
                vip_tier=None,
                cap_to_tier=True,
            )

        reservation.settled_cost = final
        reservation.committed = True
        return final

    async def post_refund(self, reservation: BillingReservation, *, model_name: str) -> None:
        """统计表最近一条 failed/cancelled 未退记录 → refunded=True。"""
        from ...utils.database.models import RHComfyuiTaskRecord

        try:
            ok = await RHComfyuiTaskRecord.mark_last_failed_refunded(
                user_id=reservation.context.user_id,
                bot_id=reservation.context.bot_id,
                task_name=model_name,
            )
            if ok:
                try:
                    from ...utils.database.stats_cache import invalidate_stats_cache

                    await invalidate_stats_cache(bot_id=reservation.context.bot_id or None)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 — 标记失败不影响主流程
            logger.warning(f"[Billing] mark refunded 失败(已忽略): {e}")


__all__ = ["PointsBillingPolicy"]
