"""对外积分 API — 外部宿主 / 其它插件与 bot 共用三桶扣费。

用法::

    from RH_ComfyUI import charge_points, refund_points, get_quota_status

    status = await charge_points(user_id, "my_bot", cost, vip_tier="basic")
    await refund_points(user_id, "my_bot", cost, vip_tier="basic")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from collections.abc import Callable, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger

from .tier_quota import normalize_tier, list_tier_quotas

if TYPE_CHECKING:
    from .tier_quota import TierQuotaDict
    from ...utils.database.models import QuotaResult, QuotaStatus, RHWalletOperation

from ...utils.database.wallet_contract import (
    WalletIntegrityError,
    WalletOperationCommand,
    WalletOperationConflict,
    validate_wallet_points,
)


async def charge_points_in_session(session: AsyncSession, command: WalletOperationCommand) -> RHWalletOperation:
    from ...utils.database.models import RHWalletOperation

    if command.kind != "charge":
        raise ValueError("charge command required")
    return await RHWalletOperation.apply_in_session(session, command)


async def settle_points_in_session(session: AsyncSession, command: WalletOperationCommand) -> RHWalletOperation:
    from ...utils.database.models import RHWalletOperation

    if command.kind != "settle":
        raise ValueError("settle command required")
    return await RHWalletOperation.apply_in_session(session, command)


async def refund_points_in_session(session: AsyncSession, command: WalletOperationCommand) -> RHWalletOperation:
    from ...utils.database.models import RHWalletOperation

    if command.kind != "refund":
        raise ValueError("refund command required")
    return await RHWalletOperation.apply_in_session(session, command)


async def charge_points_once(command: WalletOperationCommand) -> RHWalletOperation:
    from ...utils.database.models import RHWalletOperation

    if command.kind != "charge":
        raise ValueError("charge command required")
    return await RHWalletOperation.apply_once(command)


async def settle_points_once(command: WalletOperationCommand) -> RHWalletOperation:
    from ...utils.database.models import RHWalletOperation

    if command.kind != "settle":
        raise ValueError("settle command required")
    return await RHWalletOperation.apply_once(command)


async def refund_points_once(command: WalletOperationCommand) -> RHWalletOperation:
    from ...utils.database.models import RHWalletOperation

    if command.kind != "refund":
        raise ValueError("refund command required")
    return await RHWalletOperation.apply_once(command)


async def get_wallet_operation(operation_key: str) -> RHWalletOperation | None:
    from ...utils.database.models import RHWalletOperation

    return await RHWalletOperation.get_operation(operation_key)


async def get_wallet_job_operations(job_key: str) -> list[RHWalletOperation]:
    from ...utils.database.models import RHWalletOperation

    return await RHWalletOperation.get_job_operations(job_key)


class PointsDeniedError(Exception):
    """积分/额度不足。``detail`` 为 get_quota_status 形 dict + reason/need。"""

    def __init__(self, message: str, *, detail: Optional[QuotaResult] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


async def get_quota_status(
    user_id: str,
    bot_id: str,
    *,
    vip_tier: Optional[str] = None,
) -> QuotaStatus:
    from ...utils.database.models import RHBind

    return await RHBind.get_quota_status(user_id, bot_id, vip_tier=vip_tier)


async def list_quota_statuses(
    user_ids: list[str] | tuple[str, ...],
    bot_id: str,
    *,
    vip_tiers: Optional[dict[str, str]] = None,
) -> dict[str, QuotaStatus]:
    """管理端列表：一次读出 bot 池钱包，按到期规则内存算出三桶。不写库、不建账。"""
    from ...utils.database.models import RHBind

    return await RHBind.snapshot_quota_statuses(bot_id, user_ids, vip_tiers=vip_tiers)


async def charge_points(
    user_id: str,
    bot_id: str,
    amount: int,
    *,
    vip_tier: Optional[str] = None,
    reason: str = "",
) -> QuotaResult:
    """预扣三桶;不足抛 PointsDeniedError。

    返回扣后 status(含 available / buckets)。
    """
    from ...utils.database.models import RHBind

    if amount <= 0:
        raise ValueError("amount 必须 > 0")
    # vip_tier 显式传入则用;否则 RHBind 行内档位(与 bot_id 无关)
    ok, detail = await RHBind.deduct_triple(user_id, bot_id, amount, vip_tier=vip_tier)
    raw_tier = detail["tier"] if "tier" in detail else None
    tier = str(raw_tier or normalize_tier(vip_tier))
    if not ok:
        raw_reason = detail["reason"] if "reason" in detail else None
        msg = raw_reason or f"积分不足:需要 {amount}"
        logger.warning(
            f"[charge_points] denied user={user_id} bot_id={bot_id} "
            f"amount={amount} tier={tier} reason={msg!r} ({reason})"
        )
        raise PointsDeniedError(msg, detail=detail)
    avail = detail["available"] if "available" in detail else None
    logger.info(f"[charge_points] ok user={user_id} bot_id={bot_id} -{amount} tier={tier} avail={avail} ({reason})")
    return detail


async def refund_points(
    user_id: str,
    bot_id: str,
    amount: int,
    *,
    vip_tier: Optional[str] = None,
    reason: str = "",
) -> QuotaStatus:
    """三桶退回；失败向调用方传播，不能以余额查询冒充成功。"""
    from ...utils.database.models import RHBind

    validate_wallet_points(amount)
    return await RHBind.add_triple(user_id, bot_id, amount, vip_tier=vip_tier, cap_to_tier=True)


async def force_refill_points(
    user_id: str,
    bot_id: str,
    *,
    vip_tier: Optional[str] = None,
) -> QuotaStatus:
    """立刻把三桶补到当前档满额(管理端 / 手动刷新)。"""
    from ...utils.database.models import RHBind

    return await RHBind.force_refill(user_id, bot_id, vip_tier=vip_tier)


async def set_vip_tier(
    user_id: str,
    bot_id: str,
    tier: str,
    *,
    refill: bool = True,
) -> QuotaStatus:
    """设置某池额度档(free/basic/pro/enterprise/special/unlimited),与 bot_id 无关。"""
    from ...utils.database.models import RHBind

    return await RHBind.set_vip_tier(user_id, bot_id, tier, refill=refill)


async def refill_buckets(
    user_id: str,
    bot_id: str,
    buckets: list[str] | str = "all",
    *,
    vip_tier: Optional[str] = None,
) -> QuotaStatus:
    """补满指定桶 h5/day/week 或 all。"""
    from ...utils.database.models import RHBind

    return await RHBind.refill_buckets(user_id, bot_id, buckets, vip_tier=vip_tier)


async def force_refill_bot_pool(
    bot_id: str,
    *,
    default_vip_tier: str = "free",
    vip_tier_resolver: Callable[[str], Awaitable[str | None]] | None = None,
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


def get_all_tier_quotas() -> dict[str, TierQuotaDict]:
    """供 /vip/tiers 展示:各档三桶 cap。"""
    out: dict[str, TierQuotaDict] = {}
    for k, q in list_tier_quotas().items():
        out[k] = q.as_dict()
    return out


__all__ = [
    "get_wallet_operation",
    "get_wallet_job_operations",
    "WalletOperationCommand",
    "WalletOperationConflict",
    "WalletIntegrityError",
    "charge_points_once",
    "settle_points_once",
    "refund_points_once",
    "charge_points_in_session",
    "settle_points_in_session",
    "refund_points_in_session",
    "PointsDeniedError",
    "get_quota_status",
    "list_quota_statuses",
    "charge_points",
    "refund_points",
    "force_refill_points",
    "force_refill_bot_pool",
    "set_vip_tier",
    "refill_buckets",
    "get_all_tier_quotas",
]
