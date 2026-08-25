"""对外积分 API — 外部宿主 / 其它插件与 bot 共用三桶扣费。

用法::

    from RH_ComfyUI import charge_points, refund_points, get_quota_status

    status = await charge_points(user_id, "my_bot", cost, vip_tier="basic")
    await refund_points(user_id, "my_bot", cost, vip_tier="basic")
"""

from __future__ import annotations

from typing import Any, Optional

from gsuid_core.logger import logger

from .tier_quota import normalize_tier, list_tier_quotas


class PointsDeniedError(Exception):
    """积分/额度不足。``detail`` 为 get_quota_status 形 dict + reason/need。"""

    def __init__(self, message: str, *, detail: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


async def get_quota_status(
    user_id: str,
    bot_id: str,
    *,
    vip_tier: Optional[str] = None,
) -> dict[str, Any]:
    from ...utils.database.models import RHBind

    return await RHBind.get_quota_status(user_id, bot_id, vip_tier=vip_tier)


async def charge_points(
    user_id: str,
    bot_id: str,
    amount: int,
    *,
    vip_tier: Optional[str] = None,
    reason: str = "",
) -> dict[str, Any]:
    """预扣三桶;不足抛 PointsDeniedError。

    返回扣后 status(含 available / buckets)。
    """
    from ...utils.database.models import RHBind

    if amount <= 0:
        raise ValueError("amount 必须 > 0")
    # vip_tier 显式传入则用;否则 RHBind 行内档位(与 bot_id 无关)
    ok, detail = await RHBind.deduct_triple(user_id, bot_id, amount, vip_tier=vip_tier)
    tier = str((detail or {}).get("tier") or normalize_tier(vip_tier))
    if not ok:
        msg = detail.get("reason") or f"积分不足:需要 {amount}"
        logger.warning(
            f"[charge_points] denied user={user_id} bot_id={bot_id} "
            f"amount={amount} tier={tier} reason={msg!r} ({reason})"
        )
        raise PointsDeniedError(msg, detail=detail)
    logger.info(
        f"[charge_points] ok user={user_id} bot_id={bot_id} -{amount} "
        f"tier={tier} avail={detail.get('available')} ({reason})"
    )
    return detail


async def refund_points(
    user_id: str,
    bot_id: str,
    amount: int,
    *,
    vip_tier: Optional[str] = None,
    reason: str = "",
) -> dict[str, Any]:
    """三桶退回(封顶档位 cap)。失败只打日志并尽量返回当前 status。"""
    from ...utils.database.models import RHBind

    if amount <= 0:
        return await get_quota_status(user_id, bot_id, vip_tier=vip_tier)
    try:
        st = await RHBind.add_triple(user_id, bot_id, amount, vip_tier=vip_tier, cap_to_tier=True)
        logger.info(
            f"[refund_points] ok user={user_id} bot_id={bot_id} +{amount} avail={st.get('available')} ({reason})"
        )
        return st
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[refund_points] fail user={user_id} bot_id={bot_id} amount={amount} ({reason}): {e}")
        try:
            return await get_quota_status(user_id, bot_id, vip_tier=vip_tier)
        except Exception:  # noqa: BLE001
            return {"available": -1, "error": str(e)}


async def force_refill_points(
    user_id: str,
    bot_id: str,
    *,
    vip_tier: Optional[str] = None,
) -> dict[str, Any]:
    """立刻把三桶补到当前档满额(管理端 / 手动刷新)。"""
    from ...utils.database.models import RHBind

    return await RHBind.force_refill(user_id, bot_id, vip_tier=vip_tier)


async def set_vip_tier(
    user_id: str,
    bot_id: str,
    tier: str,
    *,
    refill: bool = True,
) -> dict[str, Any]:
    """设置某池额度档(free/basic/pro/enterprise/special/unlimited),与 bot_id 无关。"""
    from ...utils.database.models import RHBind

    return await RHBind.set_vip_tier(user_id, bot_id, tier, refill=refill)


async def refill_buckets(
    user_id: str,
    bot_id: str,
    buckets: list[str] | str = "all",
    *,
    vip_tier: Optional[str] = None,
) -> dict[str, Any]:
    """补满指定桶 h5/day/week 或 all。"""
    from ...utils.database.models import RHBind

    return await RHBind.refill_buckets(user_id, bot_id, buckets, vip_tier=vip_tier)


async def force_refill_bot_pool(
    bot_id: str,
    *,
    default_vip_tier: str = "free",
    vip_tier_resolver=None,
) -> dict[str, int]:
    """批量强制补满某 bot_id 下所有已有 RHBind 行。

    vip_tier_resolver: optional async (user_id) -> tier str
    """
    from ...utils.database.models import RHBind

    rows = await RHBind.select_rows(bot_id=bot_id)
    if not rows:
        return {"scanned": 0, "refilled": 0, "errors": 0}
    refilled = 0
    errors = 0
    for row in rows:
        uid = str(row.user_id)
        try:
            tier = default_vip_tier
            if vip_tier_resolver is not None:
                tier = await vip_tier_resolver(uid) or default_vip_tier
            await RHBind.force_refill(uid, bot_id, vip_tier=tier)
            refilled += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            logger.warning(f"[force_refill_bot_pool] user={uid} err={e}")
    return {"scanned": len(rows), "refilled": refilled, "errors": errors}


def get_all_tier_quotas() -> dict[str, Any]:
    """供 /vip/tiers 展示:各档三桶 cap。"""
    out: dict[str, Any] = {}
    for k, q in list_tier_quotas().items():
        out[k] = q.as_dict()
    return out


__all__ = [
    "PointsDeniedError",
    "get_quota_status",
    "charge_points",
    "refund_points",
    "force_refill_points",
    "force_refill_bot_pool",
    "set_vip_tier",
    "refill_buckets",
    "get_all_tier_quotas",
]
