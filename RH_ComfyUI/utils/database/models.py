from __future__ import annotations

import json
import hashlib
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Sequence, TypedDict
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, col
from sqlalchemy import (
    JSON,
    Index,
    Column,
    ColumnElement,
    CheckConstraint,
    UniqueConstraint,
    and_,
    case,
    func,
    text,
    delete,
    select,
    update,
)
from sqlalchemy.orm import defer
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.utils.database.base_models import Bind, BaseIDModel, with_session, with_read_session

from ..core.request import TaskType
from .wallet_contract import (
    MAX_WALLET_POINTS,
    WalletIntegrityError,
    WalletOperationCommand,
    WalletOperationConflict,
    validate_wallet_points,
)
from ...rh_config.comfyui_config import PLUGIN_CONFIG

if TYPE_CHECKING:
    from ...core.billing.tier_quota import TierQuotas


class QuotaBucket(TypedDict):
    balance: int
    cap: int
    next_refresh_at: int
    unlimited: bool


class TimedQuotaBucket(QuotaBucket):
    timer_started_at: int
    timer_active: bool


class QuotaBuckets(TypedDict):
    h5: TimedQuotaBucket
    day: QuotaBucket
    week: QuotaBucket


class QuotaRefreshTimes(TypedDict):
    h5: int
    day: int
    week: int


class QuotaStatus(TypedDict):
    available: int
    point: int
    tier: str
    label: str
    unlimited: bool
    buckets: QuotaBuckets
    refreshed_at: QuotaRefreshTimes


class QuotaResult(QuotaStatus, total=False):
    ok: bool
    need: int
    reason: str
    short_buckets: list[str]


class TaskSummary(TypedDict):
    """RHComfyuiTaskRecord.get_summary() 的固定返回结构。

    字段:
      total / success / failed / success_rate / avg_elapsed_ms — 任务量与质量
      total_points — 时间窗内 point_cost 合计
      active_users — 去重 user_id 数
      avg_points — total_points / total(无任务时为 0)
      by_task_type — 各 task_type 计数
    """

    total: int
    success: int
    failed: int
    success_rate: float
    avg_elapsed_ms: int
    total_points: int
    active_users: int
    avg_points: float
    by_task_type: dict[str, int]


DEFAULT_POINT: int = PLUGIN_CONFIG.get_config("Default_Point").data


class RHBind(Bind, table=True):
    """积分绑定表 — 三重余额(5h / 日 / 周)。

    ``point`` 为兼容字段,恒等于 min(point_5h, point_day, point_week)。
    扣费/退款/查询请优先走 ``deduct_triple`` / ``add_triple`` / ``get_quota_status``;
    旧 ``deduct_point`` / ``add_point`` / ``get_point`` 已转发到三桶逻辑。
    """

    __table_args__ = {"extend_existing": True}
    point: int = Field(default=20, title="可用积分(min三桶)")
    point_5h: int = Field(default=0, title="5小时桶余额")
    point_day: int = Field(default=0, title="日桶余额")
    point_week: int = Field(default=0, title="周桶余额")
    # 5h 计时语义(与日/周固定日历不同):
    #   refreshed_at_5h == 0 → 未开始计时(满额闲置,用后才启动)
    #   refreshed_at_5h  > 0 → 首次消费时刻,经过 Quota_5h_Seconds 后补满并清零
    refreshed_at_5h: int = Field(default=0, title="5h计时起点unix(0=未计时)")
    refreshed_at_day: int = Field(default=0, title="日桶上次补满unix")
    refreshed_at_week: int = Field(default=0, title="周桶上次补满unix")
    vip_tier: str = Field(
        default="free",
        title="额度档 free/basic/pro/enterprise/special/unlimited",
        max_length=16,
    )

    # 钱包读写先保留写事务；周期刷新与资金动作共用同一个行主键。

    @classmethod
    async def begin_write(cls, session: AsyncSession) -> None:
        if session.get_bind().dialect.name == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
        else:
            await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})

    @classmethod
    def _bucket_vals(cls, row: RHBind) -> tuple[int, int, int]:
        return row.point_5h, row.point_day, row.point_week

    @classmethod
    def _sync_available(cls, h5: int, day: int, week: int) -> int:
        return max(0, min(h5, day, week))

    @classmethod
    async def _wallet_in_session(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: str | None = None,
        create: bool = True,
        initial_point: int | None = None,
    ) -> RHBind:
        from ...core.billing.tier_quota import (
            now_ts,
            normalize_tier,
            get_tier_quotas,
            start_of_local_day,
            start_of_local_week,
        )

        rows = list(
            (
                await session.execute(
                    select(cls).where(col(cls.user_id) == user_id, col(cls.bot_id) == bot_id).limit(2).with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(rows) > 1:
            raise WalletIntegrityError("WALLET_BINDING_NOT_UNIQUE")
        if rows:
            return rows[0]
        if not create:
            raise WalletIntegrityError("WALLET_BINDING_MISSING")
        tier = normalize_tier(vip_tier)
        quotas = get_tier_quotas(tier)
        n = now_ts()
        day = max(initial_point, quotas.day) if initial_point is not None else quotas.day
        row = cls(
            user_id=user_id,
            bot_id=bot_id,
            group_id=None,
            vip_tier=tier,
            point=cls._sync_available(quotas.h5, day, quotas.week),
            point_5h=quotas.h5,
            point_day=day,
            point_week=quotas.week,
            refreshed_at_5h=0,
            refreshed_at_day=start_of_local_day(n),
            refreshed_at_week=start_of_local_week(n),
        )
        session.add(row)
        await session.flush()
        return row

    @classmethod
    def _row_status(cls, row: RHBind, tier: str | None = None) -> QuotaStatus:
        from ...core.billing.tier_quota import normalize_tier, get_tier_quotas

        resolved = normalize_tier(tier if tier is not None else row.vip_tier)
        h5, day, week = cls._bucket_vals(row)
        return cls._status_dict(
            available=cls._sync_available(h5, day, week),
            h5=h5,
            day=day,
            week=week,
            quotas=get_tier_quotas(resolved),
            r5=row.refreshed_at_5h,
            rd=row.refreshed_at_day,
            rw=row.refreshed_at_week,
            tier=resolved,
        )

    @classmethod
    def _apply_lazy_refresh(
        cls,
        *,
        h5: int,
        day: int,
        week: int,
        r5: int,
        rd: int,
        rw: int,
        point: int,
        quotas: TierQuotas,
        now: int,
        force: bool = False,
    ) -> tuple[int, int, int, int, int, int]:
        """周期补桶的纯计算。不写库。force=True 时三桶立刻补满。"""
        from ...core.billing.tier_quota import (
            needs_5h_refresh,
            needs_day_refresh,
            needs_week_refresh,
            start_of_local_day,
            start_of_local_week,
        )

        if quotas.unlimited:
            return h5, day, week, r5, rd, rw
        if rd <= 0 and rw <= 0 and h5 <= 0 and day <= 0 and week <= 0:
            return (
                quotas.h5,
                max(point, quotas.day),
                quotas.week,
                0,
                start_of_local_day(now),
                start_of_local_week(now),
            )
        if h5 >= quotas.h5 and r5 > 0 and not force:
            r5 = 0
        if force or needs_5h_refresh(r5, now):
            h5, r5 = quotas.h5, 0
        if force or needs_day_refresh(rd, now):
            day, rd = quotas.day, start_of_local_day(now)
        if force or needs_week_refresh(rw, now):
            week, rw = quotas.week, start_of_local_week(now)
        return h5, day, week, r5, rd, rw

    @classmethod
    def snapshot_quota_status(
        cls,
        row: RHBind | None,
        *,
        vip_tier: str | None = None,
        now: int | None = None,
    ) -> QuotaStatus:
        """管理端列表用：按行内数字 + 到期规则算出展示值，不建账、不 BEGIN IMMEDIATE。

        无钱包行视为从未消费，展示当前档满额（与首次 ensure_refreshed 建账结果一致）。
        """
        from ...core.billing.tier_quota import now_ts, normalize_tier, get_tier_quotas

        n = now if now is not None else now_ts()
        if row is None:
            tier = normalize_tier(vip_tier)
            quotas = get_tier_quotas(tier)
            h5, day, week, r5, rd, rw = cls._apply_lazy_refresh(
                h5=0,
                day=0,
                week=0,
                r5=0,
                rd=0,
                rw=0,
                point=0,
                quotas=quotas,
                now=n,
            )
            return cls._status_dict(
                available=cls._sync_available(h5, day, week),
                h5=h5,
                day=day,
                week=week,
                quotas=quotas,
                r5=r5,
                rd=rd,
                rw=rw,
                tier=tier,
            )
        tier = normalize_tier(vip_tier if vip_tier is not None else row.vip_tier)
        quotas = get_tier_quotas(tier)
        h5, day, week, r5, rd, rw = cls._apply_lazy_refresh(
            h5=row.point_5h,
            day=row.point_day,
            week=row.point_week,
            r5=row.refreshed_at_5h,
            rd=row.refreshed_at_day,
            rw=row.refreshed_at_week,
            point=row.point,
            quotas=quotas,
            now=n,
        )
        return cls._status_dict(
            available=cls._sync_available(h5, day, week),
            h5=h5,
            day=day,
            week=week,
            quotas=quotas,
            r5=r5,
            rd=rd,
            rw=rw,
            tier=tier,
        )

    @classmethod
    @with_read_session
    async def snapshot_quota_statuses(
        cls,
        session: AsyncSession,
        bot_id: str,
        user_ids: Sequence[str],
        *,
        vip_tiers: dict[str, str] | None = None,
    ) -> dict[str, QuotaStatus]:
        """一次读出 bot 池钱包并算展示值。必须在 session 内转成 dict,commit 后 ORM 行不可用。"""
        ids = [str(uid) for uid in user_ids if str(uid)]
        if not ids:
            return {}
        wanted = set(ids)
        # 不按 user_id IN 绑参：SQLite 里该列可能是 TEXT 也可能被写成整数，IN ('12') 对不上 12。
        rows = (await session.execute(select(cls).where(col(cls.bot_id) == bot_id))).scalars().all()
        by_uid: dict[str, RHBind] = {}
        for row in rows:
            key = str(row.user_id)
            if key in wanted and key not in by_uid:
                by_uid[key] = row
        tiers = vip_tiers or {}
        return {uid: cls.snapshot_quota_status(by_uid.get(uid), vip_tier=tiers.get(uid)) for uid in dict.fromkeys(ids)}

    @classmethod
    async def _atomic_deduct_buckets_in_session(
        cls,
        session: AsyncSession,
        row: RHBind,
        amount: int,
        now_ts_val: int,
    ) -> None:
        if row.id is None:
            raise WalletIntegrityError("WALLET_BINDING_MISSING")
        result = await session.execute(
            update(cls)
            .where(
                col(cls.id) == row.id,
                col(cls.point_5h) >= amount,
                col(cls.point_day) >= amount,
                col(cls.point_week) >= amount,
            )
            .values(
                point_5h=col(cls.point_5h) - amount,
                point_day=col(cls.point_day) - amount,
                point_week=col(cls.point_week) - amount,
                point=func.min(col(cls.point_5h) - amount, col(cls.point_day) - amount, col(cls.point_week) - amount),
                refreshed_at_5h=case(
                    (col(cls.refreshed_at_5h) <= 0, now_ts_val),
                    else_=col(cls.refreshed_at_5h),
                ),
            )
            .execution_options(synchronize_session=False)
        )
        if not isinstance(result, CursorResult) or result.rowcount != 1:
            raise WalletIntegrityError("WALLET_UPDATE_FAILED")
        await session.refresh(row)

    @classmethod
    async def _atomic_add_buckets_in_session(
        cls,
        session: AsyncSession,
        row: RHBind,
        amount: int,
        *,
        cap_h5: int,
        cap_day: int,
        cap_week: int,
    ) -> None:
        if row.id is None:
            raise WalletIntegrityError("WALLET_BINDING_MISSING")
        h5 = case(
            (col(cls.point_5h) > cap_h5, col(cls.point_5h)),
            else_=func.min(col(cls.point_5h) + amount, cap_h5),
        )
        day = case(
            (col(cls.point_day) > cap_day, col(cls.point_day)),
            else_=func.min(col(cls.point_day) + amount, cap_day),
        )
        week = case(
            (col(cls.point_week) > cap_week, col(cls.point_week)),
            else_=func.min(col(cls.point_week) + amount, cap_week),
        )
        result = await session.execute(
            update(cls)
            .where(col(cls.id) == row.id)
            .values(
                point_5h=h5,
                point_day=day,
                point_week=week,
                point=func.min(h5, day, week),
            )
            .execution_options(synchronize_session=False)
        )
        if not isinstance(result, CursorResult) or result.rowcount != 1:
            raise WalletIntegrityError("WALLET_UPDATE_FAILED")
        await session.refresh(row)

    @classmethod
    async def ensure_refreshed_in_session(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: str | None = None,
        force: bool = False,
    ) -> QuotaStatus:
        from ...core.billing.tier_quota import now_ts, normalize_tier, get_tier_quotas

        row = await cls._wallet_in_session(session, user_id, bot_id, vip_tier=vip_tier)
        tier = normalize_tier(vip_tier if vip_tier is not None else row.vip_tier)
        quotas = get_tier_quotas(tier)
        n = now_ts()
        row.vip_tier = tier
        if not quotas.unlimited:
            h5, day, week, r5, rd, rw = cls._apply_lazy_refresh(
                h5=row.point_5h,
                day=row.point_day,
                week=row.point_week,
                r5=row.refreshed_at_5h,
                rd=row.refreshed_at_day,
                rw=row.refreshed_at_week,
                point=row.point,
                quotas=quotas,
                now=n,
                force=force,
            )
            row.point_5h, row.point_day, row.point_week = h5, day, week
            row.refreshed_at_5h, row.refreshed_at_day, row.refreshed_at_week = r5, rd, rw
            row.point = cls._sync_available(h5, day, week)
        session.add(row)
        await session.flush()
        return cls._row_status(row, tier)

    @classmethod
    @with_session
    async def ensure_refreshed(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: str | None = None,
        force: bool = False,
    ) -> QuotaStatus:
        await cls.begin_write(session)
        return await cls.ensure_refreshed_in_session(session, user_id, bot_id, vip_tier=vip_tier, force=force)

    @classmethod
    def _status_dict(
        cls,
        *,
        available: int,
        h5: int,
        day: int,
        week: int,
        quotas: TierQuotas,
        r5: int,
        rd: int,
        rw: int,
        tier: str,
    ) -> QuotaStatus:
        from ...core.billing.tier_quota import (
            next_5h_refresh_at,
            next_day_refresh_at,
            next_week_refresh_at,
        )

        unlimited = bool(quotas.unlimited)
        return {
            "available": int(available),
            "point": int(available),
            "tier": tier,
            "label": quotas.label,
            "unlimited": unlimited,
            "buckets": {
                "h5": {
                    "balance": int(h5),
                    "cap": int(quotas.h5),
                    # 0 = 未开始计时(满额闲置);>0 = 预计补满 unix
                    "next_refresh_at": 0 if unlimited else next_5h_refresh_at(r5),
                    "timer_started_at": int(r5),
                    "timer_active": bool(r5 > 0) and not unlimited,
                    "unlimited": unlimited,
                },
                "day": {
                    "balance": int(day),
                    "cap": int(quotas.day),
                    "next_refresh_at": 0 if unlimited else next_day_refresh_at(),
                    "unlimited": unlimited,
                },
                "week": {
                    "balance": int(week),
                    "cap": int(quotas.week),
                    "next_refresh_at": 0 if unlimited else next_week_refresh_at(),
                    "unlimited": unlimited,
                },
            },
            "refreshed_at": {
                "h5": int(r5),  # 5h:计时起点;0=未计时
                "day": int(rd),
                "week": int(rw),
            },
        }

    @classmethod
    async def get_quota_status(
        cls,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: Optional[str] = None,
    ) -> QuotaStatus:
        return await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier)

    @classmethod
    async def deduct_triple_in_session(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        amount: int,
        *,
        vip_tier: str | None = None,
    ) -> tuple[bool, QuotaResult]:
        from ...core.billing.tier_quota import now_ts

        validate_wallet_points(amount)
        status = await cls.ensure_refreshed_in_session(session, user_id, bot_id, vip_tier=vip_tier)
        if amount == 0 or status["unlimited"]:
            return True, {**status}
        row = await cls._wallet_in_session(session, user_id, bot_id, create=False)
        h5, day, week = cls._bucket_vals(row)
        short = [name for name, balance in (("h5", h5), ("day", day), ("week", week)) if balance < amount]
        if short:
            return False, {
                **status,
                "ok": False,
                "need": amount,
                "reason": "额度不足",
                "short_buckets": short,
            }
        await cls._atomic_deduct_buckets_in_session(session, row, amount, now_ts())
        return True, {**cls._row_status(row)}

    @classmethod
    @with_session
    async def deduct_triple(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        amount: int,
        *,
        vip_tier: str | None = None,
    ) -> tuple[bool, QuotaResult]:
        await cls.begin_write(session)
        return await cls.deduct_triple_in_session(session, user_id, bot_id, amount, vip_tier=vip_tier)

    @classmethod
    async def add_triple_in_session(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        amount: int,
        *,
        vip_tier: str | None = None,
        cap_to_tier: bool = True,
        create: bool = True,
    ) -> QuotaStatus:
        from ...core.billing.tier_quota import normalize_tier, get_tier_quotas

        validate_wallet_points(amount)
        row = await cls._wallet_in_session(session, user_id, bot_id, vip_tier=vip_tier, create=create)
        tier = normalize_tier(vip_tier if vip_tier is not None else row.vip_tier)
        quotas = get_tier_quotas(tier)
        if amount > 0 and not quotas.unlimited:
            await cls._atomic_add_buckets_in_session(
                session,
                row,
                amount,
                cap_h5=quotas.h5 if cap_to_tier else MAX_WALLET_POINTS,
                cap_day=quotas.day if cap_to_tier else MAX_WALLET_POINTS,
                cap_week=quotas.week if cap_to_tier else MAX_WALLET_POINTS,
            )
        return cls._row_status(row, tier)

    @classmethod
    @with_session
    async def add_triple(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        amount: int,
        *,
        vip_tier: str | None = None,
        cap_to_tier: bool = True,
    ) -> QuotaStatus:
        """退回三桶但不触发周期刷新；写入失败必须向外传播。"""
        await cls.begin_write(session)
        return await cls.add_triple_in_session(
            session,
            user_id,
            bot_id,
            amount,
            vip_tier=vip_tier,
            cap_to_tier=cap_to_tier,
        )

    @classmethod
    async def force_refill(
        cls,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: str | None = None,
    ) -> QuotaStatus:
        return await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier, force=True)

    @classmethod
    @with_session
    async def refill_buckets(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        buckets: list[str] | tuple[str, ...] | str = "all",
        *,
        vip_tier: str | None = None,
    ) -> QuotaStatus:
        from ...core.billing.tier_quota import now_ts, get_tier_quotas, start_of_local_day, start_of_local_week

        await cls.begin_write(session)
        status = await cls.ensure_refreshed_in_session(session, user_id, bot_id, vip_tier=vip_tier)
        if status["unlimited"]:
            return status
        raw_keys = (
            (["h5", "day", "week"] if buckets in ("all", "*") else [buckets])
            if isinstance(buckets, str)
            else list(buckets)
        )
        keys = {key.strip().lower() for key in raw_keys}
        row = await cls._wallet_in_session(session, user_id, bot_id, create=False)
        quotas = get_tier_quotas(status["tier"])
        n = now_ts()
        if "h5" in keys:
            row.point_5h, row.refreshed_at_5h = quotas.h5, 0
        if "day" in keys:
            row.point_day, row.refreshed_at_day = quotas.day, start_of_local_day(n)
        if "week" in keys:
            row.point_week, row.refreshed_at_week = quotas.week, start_of_local_week(n)
        row.point = cls._sync_available(*cls._bucket_vals(row))
        session.add(row)
        await session.flush()
        return cls._row_status(row)

    @classmethod
    @with_session
    async def set_vip_tier(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        tier: str,
        *,
        refill: bool = True,
    ) -> QuotaStatus:
        from ...core.billing.tier_quota import normalize_tier

        await cls.begin_write(session)
        return await cls.ensure_refreshed_in_session(
            session,
            user_id,
            bot_id,
            vip_tier=normalize_tier(tier),
            force=refill,
        )

    # ── 兼容旧 API ───────────────────────────────────────────────

    @classmethod
    @with_session
    async def create_data(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        point: int | None = None,
        *,
        vip_tier: str = "free",
    ) -> RHBind:
        if point is not None:
            validate_wallet_points(point)
        await cls.begin_write(session)
        return await cls._wallet_in_session(
            session,
            user_id,
            bot_id,
            vip_tier=vip_tier,
            initial_point=point,
        )

    @classmethod
    async def add_point(
        cls,
        user_id: str,
        bot_id: str,
        add_point_num: int,
    ) -> int:
        """兼容旧接口:三桶同加并封顶档位。"""
        await cls.add_triple(user_id, bot_id, add_point_num, cap_to_tier=True)
        return 0

    @classmethod
    async def get_point(
        cls,
        user_id: str,
        bot_id: str,
    ) -> int:
        """兼容旧接口:懒刷新后返回 available。"""
        st = await cls.ensure_refreshed(user_id, bot_id)
        return int(st.get("available") or 0)

    @classmethod
    async def deduct_point(
        cls,
        user_id: str,
        bot_id: str,
        deduct_point_num: int,
        initial_point: int = DEFAULT_POINT,
        *,
        vip_tier: Optional[str] = None,
    ) -> bool:
        """兼容旧接口:三桶同扣。``initial_point`` 忽略(建账走档位满额)。"""
        del initial_point  # 三重余额下建账由 ensure_refreshed/create_data 按档位初始化
        ok, _ = await cls.deduct_triple(user_id, bot_id, deduct_point_num, vip_tier=vip_tier)
        return ok


class WalletReceipt(TypedDict):
    schema_version: str
    operation_key: str
    job_key: str
    external_ref: str
    kind: str
    operation_version: int
    user_id: str
    bot_id: str
    request_hash: str
    command_hash: str
    price_revision: str
    status: str
    reason: str
    requested_target_points: int
    billed_delta_points: int
    previous_net_points: int
    net_after_points: int
    quota_mode: str
    bucket_before: dict[str, int]
    bucket_after: dict[str, int]
    bucket_deltas: dict[str, int]
    available_snapshot: int
    predecessor_operation_keys: list[str]
    occurred_at: str


class RHWalletOperation(BaseIDModel, table=True):
    """原钱包事务的不可变操作凭证；不持有另一份余额。"""

    __table_args__ = (
        UniqueConstraint("operation_key", name="uq_rhwalletoperation_key"),
        UniqueConstraint("job_key", "kind", "operation_version", name="uq_rhwalletoperation_job_kind_version"),
        CheckConstraint("schema_version = 'rh-wallet-op/v1' AND operation_version = 1", name="ck_rh_wallet_version"),
        CheckConstraint("kind IN ('charge','settle','refund')", name="ck_rh_wallet_kind"),
        CheckConstraint("status IN ('committed','declined')", name="ck_rh_wallet_status"),
        CheckConstraint("quota_mode IN ('capped','unlimited')", name="ck_rh_wallet_quota_mode"),
        CheckConstraint("requested_target_points BETWEEN 0 AND 2000000000", name="ck_rh_wallet_target"),
        CheckConstraint("billed_delta_points BETWEEN -2000000000 AND 2000000000", name="ck_rh_wallet_delta"),
        CheckConstraint("net_after_points BETWEEN 0 AND 2000000000", name="ck_rh_wallet_net"),
        CheckConstraint("previous_net_points BETWEEN 0 AND 2000000000", name="ck_rh_wallet_previous_net"),
        CheckConstraint("net_after_points = previous_net_points + billed_delta_points", name="ck_rh_wallet_net_delta"),
        CheckConstraint("status != 'declined' OR billed_delta_points = 0", name="ck_rh_wallet_declined"),
        CheckConstraint("kind != 'charge' OR billed_delta_points >= 0", name="ck_rh_wallet_charge"),
        CheckConstraint(
            "kind != 'refund' OR (requested_target_points = 0 AND billed_delta_points <= 0)",
            name="ck_rh_wallet_refund",
        ),
        CheckConstraint(
            "length(request_hash) = 64 AND length(command_hash) = 64 AND length(receipt_digest) = 64",
            name="ck_rh_wallet_digests",
        ),
        Index("ix_rhwalletoperation_bot_occurred", "bot_id", "occurred_at"),
        Index("ix_rhwalletoperation_bot_user_occurred", "bot_id", "user_id", "occurred_at"),
        {"extend_existing": True},
    )
    schema_version: str = Field(default="rh-wallet-op/v1")
    operation_key: str = Field(max_length=256)
    job_key: str = Field(max_length=256, index=True)
    external_ref: str = Field(max_length=256, index=True)
    kind: str = Field(max_length=16)
    operation_version: int = Field(default=1)
    user_id: str = Field(max_length=256, index=True)
    bot_id: str = Field(max_length=256)
    request_hash: str = Field(max_length=64)
    command_hash: str = Field(max_length=64)
    price_revision: str = Field(max_length=128)
    status: str = Field(max_length=16)
    reason: str = Field(default="", max_length=64)
    requested_target_points: int
    billed_delta_points: int
    previous_net_points: int
    net_after_points: int
    quota_mode: str = Field(max_length=16)
    bucket_before: dict[str, int] = Field(sa_column=Column(JSON, nullable=False))
    bucket_after: dict[str, int] = Field(sa_column=Column(JSON, nullable=False))
    bucket_deltas: dict[str, int] = Field(sa_column=Column(JSON, nullable=False))
    available_snapshot: int
    predecessor_operation_keys: list[str] = Field(sa_column=Column(JSON, nullable=False))
    occurred_at: str
    receipt_digest: str = Field(max_length=64)

    def receipt_body(self) -> WalletReceipt:
        return {
            "schema_version": self.schema_version,
            "operation_key": self.operation_key,
            "job_key": self.job_key,
            "external_ref": self.external_ref,
            "kind": self.kind,
            "operation_version": self.operation_version,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "request_hash": self.request_hash,
            "command_hash": self.command_hash,
            "price_revision": self.price_revision,
            "status": self.status,
            "reason": self.reason,
            "requested_target_points": self.requested_target_points,
            "billed_delta_points": self.billed_delta_points,
            "previous_net_points": self.previous_net_points,
            "net_after_points": self.net_after_points,
            "quota_mode": self.quota_mode,
            "bucket_before": self.bucket_before,
            "bucket_after": self.bucket_after,
            "bucket_deltas": self.bucket_deltas,
            "available_snapshot": self.available_snapshot,
            "predecessor_operation_keys": self.predecessor_operation_keys,
            "occurred_at": self.occurred_at,
        }

    @classmethod
    @with_read_session
    async def get_operation(cls, session: AsyncSession, operation_key: str) -> RHWalletOperation | None:
        return (await session.execute(select(cls).where(col(cls.operation_key) == operation_key))).scalar_one_or_none()

    @classmethod
    @with_read_session
    async def get_job_operations(cls, session: AsyncSession, job_key: str) -> list[RHWalletOperation]:
        return list(
            (await session.execute(select(cls).where(col(cls.job_key) == job_key).order_by(col(cls.id))))
            .scalars()
            .all()
        )

    @classmethod
    @with_session
    async def reconcile_record_once(
        cls,
        session: AsyncSession,
        record_id: int,
        actual: int,
        *,
        adjust_wallet: bool = True,
    ) -> tuple[RHWalletOperation, bool] | None:
        """新协议统计与差额共用原 settle；无回执旧行仍由旧维护路径处理。"""
        validate_wallet_points(actual)
        await RHBind.begin_write(session)
        record = (
            await session.execute(
                select(RHComfyuiTaskRecord).where(col(RHComfyuiTaskRecord.id) == record_id).with_for_update()
            )
        ).scalar_one_or_none()
        if record is None or not record.trace_id:
            return None
        charges = list(
            (
                await session.execute(
                    select(cls)
                    .where(
                        col(cls.external_ref) == record.trace_id,
                        col(cls.kind) == "charge",
                    )
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if len(charges) > 1:
            raise WalletIntegrityError("WALLET_REFERENCE_NOT_UNIQUE")
        if not charges:
            return None
        charge = charges[0]
        if charge.user_id != record.user_id or charge.bot_id != record.bot_id:
            raise WalletOperationConflict("WALLET_BILLING_CONTEXT_CONFLICT")
        if charge.status != "committed":
            raise WalletOperationConflict("WALLET_COMMITTED_CHARGE_REQUIRED")
        if not adjust_wallet:
            current = (
                await session.execute(
                    select(cls)
                    .where(col(cls.job_key) == charge.job_key, col(cls.status) == "committed")
                    .order_by(col(cls.id).desc())
                    .limit(1)
                )
            ).scalar_one()
            return current, True
        command = WalletOperationCommand(
            operation_key=f"{charge.job_key}:settle:v1",
            job_key=charge.job_key,
            external_ref=charge.external_ref,
            kind="settle",
            user_id=charge.user_id,
            bot_id=charge.bot_id,
            request_hash=charge.request_hash,
            price_revision=charge.price_revision,
            requested_target_points=actual,
        )
        prior = (
            await session.execute(select(cls).where(col(cls.operation_key) == command.operation_key))
        ).scalar_one_or_none()
        operation = await cls.apply_in_session(session, command)
        record.point_cost = operation.net_after_points
        session.add(record)
        await session.flush()
        return operation, prior is not None

    @classmethod
    @with_session
    async def apply_once(cls, session: AsyncSession, command: WalletOperationCommand) -> RHWalletOperation:
        await RHBind.begin_write(session)
        return await cls.apply_in_session(session, command)

    @classmethod
    async def apply_in_session(cls, session: AsyncSession, command: WalletOperationCommand) -> RHWalletOperation:
        """调用方须先取得写事务；本方法不提交、不发外部请求。"""
        existing = (
            await session.execute(select(cls).where(col(cls.operation_key) == command.operation_key))
        ).scalar_one_or_none()
        if existing is not None:
            if existing.command_hash != command.command_hash:
                raise WalletOperationConflict("WALLET_OPERATION_KEY_CONFLICT")
            return existing

        history = list(
            (await session.execute(select(cls).where(col(cls.job_key) == command.job_key).order_by(col(cls.id))))
            .scalars()
            .all()
        )
        if any(op.kind == command.kind and op.operation_version == command.operation_version for op in history):
            raise WalletOperationConflict("WALLET_JOB_OPERATION_CONFLICT")
        charge = next((op for op in history if op.kind == "charge"), None)
        if command.kind != "charge":
            if charge is None or charge.status != "committed":
                raise WalletOperationConflict("WALLET_COMMITTED_CHARGE_REQUIRED")
            if (
                charge.user_id != command.user_id
                or charge.bot_id != command.bot_id
                or charge.external_ref != command.external_ref
                or charge.request_hash != command.request_hash
                or charge.price_revision != command.price_revision
            ):
                raise WalletOperationConflict("WALLET_BILLING_CONTEXT_CONFLICT")
        elif history:
            raise WalletOperationConflict("WALLET_CHARGE_ALREADY_EXISTS")

        committed = [op for op in history if op.status == "committed"]
        previous = sum(op.billed_delta_points for op in committed)
        validate_wallet_points(previous)
        refunded = any(op.kind == "refund" for op in committed)
        delta = command.requested_target_points - previous
        status, reason = "committed", ""
        if refunded and command.kind == "settle" and command.requested_target_points > 0:
            status, reason, delta = "declined", "already_refunded", 0

        if command.kind == "charge" or (delta > 0 and not refunded):
            before_status = await RHBind.ensure_refreshed_in_session(
                session,
                command.user_id,
                command.bot_id,
                vip_tier=command.vip_tier if command.kind == "charge" else None,
            )
        else:
            row = await RHBind._wallet_in_session(session, command.user_id, command.bot_id, create=False)
            before_status = RHBind._row_status(row)
        before = {key: before_status["buckets"][key]["balance"] for key in ("h5", "day", "week")}
        after_status = before_status
        if delta > 0:
            ok, result = await RHBind.deduct_triple_in_session(
                session,
                command.user_id,
                command.bot_id,
                delta,
                vip_tier=command.vip_tier if command.kind == "charge" else None,
            )
            after_status = result
            if not ok:
                status, reason, delta = "declined", "insufficient_points", 0
        elif delta < 0:
            after_status = await RHBind.add_triple_in_session(
                session,
                command.user_id,
                command.bot_id,
                -delta,
                create=False,
            )
        after = {key: after_status["buckets"][key]["balance"] for key in ("h5", "day", "week")}
        operation = cls(
            operation_key=command.operation_key,
            job_key=command.job_key,
            external_ref=command.external_ref,
            kind=command.kind,
            operation_version=command.operation_version,
            user_id=command.user_id,
            bot_id=command.bot_id,
            request_hash=command.request_hash,
            command_hash=command.command_hash,
            price_revision=command.price_revision,
            status=status,
            reason=reason,
            requested_target_points=command.requested_target_points,
            billed_delta_points=delta,
            previous_net_points=previous,
            net_after_points=previous + delta,
            quota_mode="unlimited" if before_status["unlimited"] else "capped",
            bucket_before=before,
            bucket_after=after,
            bucket_deltas={key: after[key] - before[key] for key in before},
            available_snapshot=after_status["available"],
            predecessor_operation_keys=[op.operation_key for op in committed],
            occurred_at=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            receipt_digest="",
        )
        operation.receipt_digest = hashlib.sha256(
            json.dumps(operation.receipt_body(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        session.add(operation)
        await session.flush()
        return operation


# 同一份启动迁移定义也供隔离升级测试执行；新表由 Core create_all 建立。
WALLET_OPERATION_MIGRATIONS = (
    "CREATE TRIGGER IF NOT EXISTS rhwalletoperation_immutable_update BEFORE UPDATE ON rhwalletoperation "
    "BEGIN SELECT RAISE(ABORT, 'WALLET_RECEIPT_IMMUTABLE'); END",
    "CREATE TRIGGER IF NOT EXISTS rhwalletoperation_immutable_delete BEFORE DELETE ON rhwalletoperation "
    "BEGIN SELECT RAISE(ABORT, 'WALLET_RECEIPT_IMMUTABLE'); END",
    "CREATE INDEX IF NOT EXISTS ix_rhwalletoperation_bot_occurred ON rhwalletoperation (bot_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS ix_rhwalletoperation_bot_user_occurred "
    "ON rhwalletoperation (bot_id, user_id, occurred_at)",
)
exec_list.extend(WALLET_OPERATION_MIGRATIONS)


@site.register_admin
class SsPushAdmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="AI绘图积分管理",
        icon="fa fa-bullhorn",
    )  # type: ignore

    # 配置管理模型
    model = RHBind


# ═══════════════════════════════════════════════════════════════════════
#  任务执行统计表
#
#  生命周期:begin_task(status=running) → record_task(终态 UPDATE/INSERT)。
#  历史行仅有终态,无 running;查询/汇总须兼容。WebConsole 与消费 API 共用。
# ═══════════════════════════════════════════════════════════════════════


class RHComfyuiTaskStatus(str, Enum):
    """任务执行状态枚举

    - running: 预扣后、执行中(begin_task 写入)
    - ok / failed / cancelled: 终态(record_task 更新或旧路径一次插入)
    历史数据仅有终态,无 running 行,查询需兼容。
    """

    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RHComfyuiTaskRecord(SQLModel, table=True):
    """RH_ComfyUI 任务执行统计记录"""

    # 复合索引: admin stats / list / TOP 用户 主路径 (bot_id + 时间/用户/状态)
    # 旧库由文末 exec_list CREATE INDEX IF NOT EXISTS 补齐
    __table_args__ = (
        Index("ix_rhcomfyuitaskrecord_bot_created", "bot_id", "created_at"),
        # covering: 时间窗聚合积分/状态/耗时/类型,避免回含 JSON 的宽行
        Index(
            "ix_rhcomfyuitaskrecord_bot_created_user_spend",
            "bot_id",
            "created_at",
            "user_id",
            "status",
            "point_cost",
            "elapsed_ms",
            "task_type",
        ),
        Index("ix_rhcomfyuitaskrecord_bot_user", "bot_id", "user_id"),
        Index("ix_rhcomfyuitaskrecord_bot_user_created", "bot_id", "user_id", "created_at"),
        Index("ix_rhcomfyuitaskrecord_bot_status_created", "bot_id", "status", "created_at"),
        Index("ix_rhcomfyuitaskrecord_user_created", "user_id", "created_at"),
        # 筛选项 DISTINCT 覆盖索引,避免扫含 64KB JSON 的宽行
        Index("ix_rhcomfyuitaskrecord_bot_pipeline", "bot_id", "task_name", "task_type"),
        Index("ix_rhcomfyuitaskrecord_bot_bmodel", "bot_id", "backend_model"),
        Index("ix_rhcomfyuitaskrecord_bot_bmodel_type", "bot_id", "backend_model", "task_type"),
        Index("ix_rhcomfyuitaskrecord_bot_backend", "bot_id", "backend"),
        Index("ix_rhcomfyuitaskrecord_trace_id", "trace_id"),
        Index("ix_rhcomfyuitaskrecord_bot_trace", "bot_id", "trace_id"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True, title="序号")

    # ── 触发者 ──
    user_id: str = Field(title="用户ID", index=True, max_length=64)
    bot_id: str = Field(default="", title="Bot 平台", index=True, max_length=64)
    group_id: str = Field(default="", title="群号(私聊为空)", index=True, max_length=64)

    # ── 任务标识 ──
    task_type: str = Field(title="任务类型 image/video/music/speech", index=True, max_length=16)
    task_name: str = Field(title="节点名(来自 NodeDef.name)", index=True, max_length=128)
    backend: str = Field(default="", title="后端 comfyui/rh_app/seedance/...", max_length=32)
    backend_model: str = Field(default="", title="实际使用的厂商模型ID", max_length=128)
    backend_provider: str = Field(default="", title="供应商(ark/gateway/runninghub/...)", max_length=32)
    backend_key_prefix: str = Field(default="", title="供应商 Key 前6位(审计用)", max_length=16)

    # ── 核心输入参数 ──
    duration_seconds: Optional[int] = Field(default=None, title="视频/音频时长(秒)")
    width: Optional[int] = Field(default=None, title="宽度")
    height: Optional[int] = Field(default=None, title="高度")
    ratio: str = Field(default="", title="宽高比(16:9/9:16/...)", max_length=16)
    resolution: str = Field(default="", title="分辨率(480p/720p/1080p)", max_length=16)
    seed: Optional[int] = Field(default=None, title="随机种子")
    voice_id: str = Field(default="", title="音色ID(仅语音)", max_length=64)
    extra_params_json: str = Field(default="", title="其他核心参数 JSON")
    # 用户提示词(由 GenerationRequest.prompt 透传,用于调用方"我的消费"页直接展示;
    # 2026-07-01 之前记录里没有这一列,已通过 exec_list 补 ALTER TABLE)
    prompt: str = Field(default="", title="生成提示词", max_length=4000)

    # ── 执行结果 ──
    status: str = Field(title="状态 running/ok/failed/cancelled", index=True, max_length=16)
    elapsed_ms: Optional[int] = Field(default=None, title="任务耗时(毫秒)")
    point_cost: int = Field(default=0, title="本次消耗积分数")
    refunded: bool = Field(default=False, title="失败时是否已退回积分")
    error_message: str = Field(default="", title="失败摘要(截断到 2KB)")

    # ── 原始数据 ──
    raw_response_json: str = Field(default="", title="厂商原始响应 JSON(已截断到 64KB)")
    # 调度入口冻结的完整 GenerationRequest;仅替换 bytes/base64 媒体内容
    # (旧记录为空串,由 exec_list 自动补列)
    request_body_json: str = Field(default="", title="原始请求体 JSON(Base64 已截断)")

    # ── 本地产物(2026-07-17 新增,exec_list 补 ALTER;旧记录为空串) ──
    # 供应商返回 base64/二进制时 raw 里没有 URL,但 executor._save_output 会把
    # 产物落盘到 OUTPUT_PATH;这里存相对 OUTPUT_PATH 的路径 JSON 数组
    # (如 ["image/1752741663123.png"]),消费详情接口按 index 映射回文件。
    saved_files_json: str = Field(default="", title="本地产物相对路径 JSON 数组")

    # ── 关联 ──
    trace_id: str = Field(default="", title="调用链追踪ID(由 GenerationRequest.trace_id 传入)", max_length=64)

    # ── 入口维度(2026-07-02 ABC 重构新增;旧记录为空串) ──
    entry_point: str = Field(default="", title="触发入口 command/agent/http", max_length=16)

    # ── 时间戳 ──
    # 单列索引保留;复合见 __table_args__
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        title="创建时间 UTC",
        index=True,
    )

    # ── 便捷属性 ──

    @property
    def is_success(self) -> bool:
        return self.status == RHComfyuiTaskStatus.OK.value

    @property
    def display_task_type(self) -> str:
        # 字符串到 TaskType 的合法枚举转换;非法值回退到原字符串
        try:
            return TaskType(self.task_type).name
        except ValueError:
            return self.task_type

    # ── 查询 / 清理方法:风格与 外部插件的 VideoJob.list_by_owner() 一致 ──

    @classmethod
    @with_read_session
    async def list_by_user(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: Optional[str] = None,
        task_type: Optional[str] = None,
        task_name: Optional[str] = None,
        status: Optional[str] = None,
        trace_id: Optional[str] = None,
        backend: Optional[str] = None,
        backend_model: Optional[str] = None,
        is_refunded: Optional[bool] = None,
        min_points: Optional[int] = None,
        max_points: Optional[int] = None,
        prompt_search: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list["RHComfyuiTaskRecord"]:
        """条件查询任务记录(按时间倒序);任一筛选条件为 None 则不加入 WHERE"""
        # 列数确定的 select 写法,避免变长 splat 导致类型退化为 Any
        conds: list[ColumnElement[bool]] = [col(cls.user_id) == user_id]
        cls._append_list_filters(
            conds,
            bot_id=bot_id,
            task_type=task_type,
            task_name=task_name,
            status=status,
            trace_id=trace_id,
            backend=backend,
            backend_model=backend_model,
            is_refunded=is_refunded,
            min_points=min_points,
            max_points=max_points,
            prompt_search=prompt_search,
            start_time=start_time,
            end_time=end_time,
        )

        stmt = (
            select(cls)
            .options(*cls._defer_heavy_columns())
            .where(and_(*conds))
            .order_by(col(cls.created_at).desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    @classmethod
    @with_read_session
    async def get_summary(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        bot_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_ids: Optional[list[str]] = None,
    ) -> TaskSummary:
        """聚合统计:任务量 / 质量 / 积分 / 活跃用户 / 类型分布。

        ⚠️ 性能:
          - 禁止 ``select(cls).subquery()`` 拖入大 JSON 列;
          - 主指标合并为 **1 条** 条件聚合 SQL(旧 5 次 COUNT/AVG 串行);
          - by_task_type 另 1 条 GROUP BY;
          - 上层 ``stats_cache`` 表缓存 + 写路径失效。
        """
        conds: list[ColumnElement[bool]] = []
        if bot_id is not None:
            conds.append(col(cls.bot_id) == bot_id)
        cls._append_user_conds(conds, user_id=user_id, user_ids=user_ids)
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)

        def _where(stmt):  # type: ignore[no-untyped-def]
            return stmt.where(and_(*conds)) if conds else stmt

        ok_v = RHComfyuiTaskStatus.OK.value
        failed_v = RHComfyuiTaskStatus.FAILED.value
        cancelled_v = RHComfyuiTaskStatus.CANCELLED.value

        # 单次扫描: total / success / failed / avg(终态 elapsed) / points / users
        agg_stmt = _where(
            select(
                func.count().label("total"),
                func.coalesce(
                    func.sum(case((col(cls.status) == ok_v, 1), else_=0)),
                    0,
                ).label("success"),
                func.coalesce(
                    func.sum(
                        case(
                            (col(cls.status).in_((failed_v, cancelled_v)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("failed"),
                func.avg(
                    case(
                        (
                            col(cls.status).in_((ok_v, failed_v, cancelled_v)),
                            col(cls.elapsed_ms),
                        ),
                        else_=None,
                    )
                ).label("avg_elapsed"),
                func.coalesce(func.sum(col(cls.point_cost)), 0).label("total_points"),
                func.count(func.distinct(col(cls.user_id))).label("active_users"),
            ).select_from(cls)
        )
        row = (await session.execute(agg_stmt)).one()
        total = int(row.total or 0)
        success = int(row.success or 0)
        failed = int(row.failed or 0)
        avg_elapsed = float(row.avg_elapsed or 0)
        total_points = int(row.total_points or 0)
        active_users = int(row.active_users or 0)

        type_rows = await session.execute(
            _where(select(col(cls.task_type), func.count()).select_from(cls).group_by(col(cls.task_type)))
        )
        by_type_pairs = type_rows.all()
        terminal_total = success + failed
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": round(success / terminal_total, 4) if terminal_total else 0.0,
            "avg_elapsed_ms": int(avg_elapsed),
            "total_points": total_points,
            "active_users": active_users,
            "avg_points": round(total_points / total, 2) if total else 0.0,
            "by_task_type": {k: int(v) for k, v in by_type_pairs if k},
        }

    @classmethod
    def _defer_heavy_columns(cls):
        """列表查询跳过大 JSON 列(详情接口懒加载 raw/request_body)。"""
        # SQLModel 把列注解成 str,defer 需要 ORM 属性;第三方 typing 缺口
        return (
            defer(cls.raw_response_json),  # type: ignore[arg-type]
            defer(cls.request_body_json),  # type: ignore[arg-type]
            defer(cls.extra_params_json),  # type: ignore[arg-type]
            defer(cls.saved_files_json),  # type: ignore[arg-type]
        )

    @classmethod
    def _append_user_conds(
        cls,
        conds: list[ColumnElement[bool]],
        user_id: Optional[str] = None,
        user_ids: Optional[list[str]] = None,
    ) -> None:
        """user_ids 非空走 IN;否则单 user_id 等值。空列表由调用方短路,这里不加条件。"""
        if user_ids:
            conds.append(col(cls.user_id).in_(list(user_ids)))
        elif user_id is not None:
            conds.append(col(cls.user_id) == user_id)

    @classmethod
    def _append_list_filters(
        cls,
        conds: list[ColumnElement[bool]],
        *,
        bot_id: Optional[str] = None,
        group_id: Optional[str] = None,
        task_type: Optional[str] = None,
        task_name: Optional[str] = None,
        status: Optional[str] = None,
        trace_id: Optional[str] = None,
        backend: Optional[str] = None,
        backend_model: Optional[str] = None,
        is_refunded: Optional[bool] = None,
        min_points: Optional[int] = None,
        max_points: Optional[int] = None,
        prompt_search: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> None:
        """列表 / 日聚合共用 WHERE。空串视为未传。

        ``task_name`` / ``backend_model`` / ``backend`` 都是精确等值
        (下拉选列表项,不是模糊搜)。
        """
        if bot_id is not None:
            conds.append(col(cls.bot_id) == bot_id)
        if group_id is not None:
            conds.append(col(cls.group_id) == group_id)
        if task_type is not None:
            conds.append(col(cls.task_type) == task_type)
        name = (task_name or "").strip()
        if name:
            conds.append(col(cls.task_name) == name)
        if status is not None:
            conds.append(col(cls.status) == status)
        if trace_id is not None:
            conds.append(col(cls.trace_id) == trace_id)
        if backend is not None:
            conds.append(col(cls.backend) == backend)
        model = (backend_model or "").strip()
        if model:
            conds.append(col(cls.backend_model) == model)
        if is_refunded is not None:
            conds.append(col(cls.refunded) == is_refunded)
        if min_points is not None:
            conds.append(col(cls.point_cost) >= min_points)
        if max_points is not None:
            conds.append(col(cls.point_cost) <= max_points)
        if prompt_search is not None:
            conds.append(col(cls.prompt).contains(prompt_search))
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)

    @classmethod
    @with_read_session
    async def list_filter_options(
        cls,
        session: AsyncSession,
        bot_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """消费筛选下拉:库里实际出现过的 Pipeline(+task_type) / 模型 / 后端。

        三条 DISTINCT 都走 (bot_id, …) 覆盖索引,不读 raw_response 等宽列。
        空串不进列表。同名 Pipeline 若出现多种 task_type,保留先见到的。
        """
        conds: list[ColumnElement[bool]] = []
        if bot_id is not None:
            conds.append(col(cls.bot_id) == bot_id)

        def _where(stmt):  # type: ignore[no-untyped-def]
            return stmt.where(and_(*conds)) if conds else stmt

        type_rank = {"image": 0, "video": 1, "music": 2, "speech": 3}

        async def _named_types(name_col) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
            stmt = _where(select(name_col, col(cls.task_type)).where(name_col != "").distinct())
            rows = (await session.execute(stmt)).all()
            first: dict[str, str] = {}
            for name, ttype in rows:
                n = str(name or "").strip()
                if not n or n in first:
                    continue
                first[n] = str(ttype or "").strip()
            return [
                {"name": n, "task_type": t}
                for n, t in sorted(
                    first.items(),
                    key=lambda kv: (type_rank.get(kv[1], 9), kv[0].lower()),
                )
            ]

        async def _distinct(column) -> list[str]:  # type: ignore[no-untyped-def]
            stmt = _where(select(column).where(column != "").distinct())
            rows = (await session.execute(stmt)).scalars().all()
            return sorted((str(v) for v in rows if v), key=str.lower)

        return {
            "pipelines": await _named_types(col(cls.task_name)),
            "models": await _named_types(col(cls.backend_model)),
            "backends": await _distinct(col(cls.backend)),
        }

    @classmethod
    @with_read_session
    async def list_all(
        cls,
        session: AsyncSession,
        user_id: Optional[str] = None,
        user_ids: Optional[list[str]] = None,
        bot_id: Optional[str] = None,
        group_id: Optional[str] = None,
        task_type: Optional[str] = None,
        task_name: Optional[str] = None,
        status: Optional[str] = None,
        trace_id: Optional[str] = None,
        backend: Optional[str] = None,
        backend_model: Optional[str] = None,
        is_refunded: Optional[bool] = None,
        min_points: Optional[int] = None,
        max_points: Optional[int] = None,
        prompt_search: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list["RHComfyuiTaskRecord"]:
        """跨用户条件查询任务记录(按时间倒序);任一筛选条件为 None 则不加入 WHERE

        与 list_by_user 的区别:不强制 user_id,用于管理员查看全员消费记录。
        大字段(raw_response/request_body 等)默认 defer,避免列表把每行 64KB 全读出。
        user_ids 非空时按 IN 过滤(姓名搜索可能命中多人);优先于单 user_id。
        """
        conds: list[ColumnElement[bool]] = []
        cls._append_user_conds(conds, user_id=user_id, user_ids=user_ids)
        cls._append_list_filters(
            conds,
            bot_id=bot_id,
            group_id=group_id,
            task_type=task_type,
            task_name=task_name,
            status=status,
            trace_id=trace_id,
            backend=backend,
            backend_model=backend_model,
            is_refunded=is_refunded,
            min_points=min_points,
            max_points=max_points,
            prompt_search=prompt_search,
            start_time=start_time,
            end_time=end_time,
        )

        stmt = select(cls).options(*cls._defer_heavy_columns())
        if conds:
            stmt = stmt.where(and_(*conds))
        stmt = stmt.order_by(col(cls.created_at).desc()).offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    @classmethod
    @with_read_session
    async def get_by_record_id(
        cls,
        session: AsyncSession,
        record_id: int,
    ) -> Optional["RHComfyuiTaskRecord"]:
        """按主键查询单条记录;不存在返回 None(供消费详情懒加载使用)"""
        stmt = select(cls).where(col(cls.id) == record_id).limit(1)
        return (await session.execute(stmt)).scalar_one_or_none()

    @classmethod
    @with_read_session
    async def get_user_summaries(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        top_n: int = 20,
        bot_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_ids: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        """按用户聚合消费摘要(供管理员查看"谁花了多少")

        返回按总积分倒序的 top_n 条:
          {
            "user_id": str,
            "total": int,
            "success": int,
            "failed": int,
            "total_points": int,
            "by_task_type": {task_type: count},
          }

        ⚠️ 性能:主榜 1 条 GROUP BY(条件聚合 success/failed);
        by_task_type 仅对 TOP user 再 1 条查询;禁止 select(cls) 子查询。
        """
        conds: list[ColumnElement[bool]] = []
        if bot_id is not None:
            conds.append(col(cls.bot_id) == bot_id)
        cls._append_user_conds(conds, user_id=user_id, user_ids=user_ids)
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)

        def _where(stmt):  # type: ignore[no-untyped-def]
            return stmt.where(and_(*conds)) if conds else stmt

        ok_v = RHComfyuiTaskStatus.OK.value
        failed_v = RHComfyuiTaskStatus.FAILED.value
        cancelled_v = RHComfyuiTaskStatus.CANCELLED.value

        # 主榜: total / points / success / failed 一次 GROUP BY
        agg_stmt = _where(
            select(
                col(cls.user_id).label("user_id"),
                func.count().label("total"),
                func.coalesce(func.sum(col(cls.point_cost)), 0).label("total_points"),
                func.coalesce(
                    func.sum(case((col(cls.status) == ok_v, 1), else_=0)),
                    0,
                ).label("success"),
                func.coalesce(
                    func.sum(
                        case(
                            (col(cls.status).in_((failed_v, cancelled_v)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("failed"),
            )
            .select_from(cls)
            .group_by(col(cls.user_id))
            .order_by(func.sum(col(cls.point_cost)).desc())
            .limit(top_n)
        )
        agg_rows = (await session.execute(agg_stmt)).all()
        if not agg_rows:
            return []

        top_uids = [r.user_id for r in agg_rows]
        uid_conds = list(conds) + [col(cls.user_id).in_(top_uids)]

        type_stmt = (
            select(col(cls.user_id), col(cls.task_type), func.count())
            .select_from(cls)
            .where(and_(*uid_conds))
            .group_by(col(cls.user_id), col(cls.task_type))
        )
        type_pairs = (await session.execute(type_stmt)).all()
        by_type_map: dict[Any, dict[str, int]] = {}
        for row_uid, ttype, cnt in type_pairs:
            if not ttype:
                continue
            bucket = by_type_map.setdefault(row_uid, {})
            bucket[str(ttype)] = int(cnt)

        results: list[dict[str, Any]] = []
        for r in agg_rows:
            uid = r.user_id
            results.append(
                {
                    "user_id": str(uid),
                    "total": int(r.total or 0),
                    "success": int(r.success or 0),
                    "failed": int(r.failed or 0),
                    "total_points": int(r.total_points or 0),
                    "by_task_type": by_type_map.get(uid, {}),
                }
            )
        return results

    @classmethod
    @with_read_session
    async def get_daily_series(
        cls,
        session: AsyncSession,
        *,
        bot_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_ids: Optional[list[str]] = None,
        group_id: Optional[str] = None,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        task_name: Optional[str] = None,
        backend: Optional[str] = None,
        backend_model: Optional[str] = None,
        is_refunded: Optional[bool] = None,
        min_points: Optional[int] = None,
        max_points: Optional[int] = None,
        prompt_search: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """按北京日历日聚合:请求数 / 失败数 / 积分合计 / 去重用户数。

        created_at 存 UTC;SQLite 用 ``strftime(..., '+8 hours')`` 切到 UTC+8 的日期。
        返回 ``[{date, requests, failed, points, users}, ...]``,按 date 升序。缺日由上层补零。
        failed 口径与 get_summary 一致:status 为 failed 或 cancelled。
        """
        conds: list[ColumnElement[bool]] = []
        cls._append_user_conds(conds, user_id=user_id, user_ids=user_ids)
        cls._append_list_filters(
            conds,
            bot_id=bot_id,
            group_id=group_id,
            status=status,
            task_type=task_type,
            task_name=task_name,
            backend=backend,
            backend_model=backend_model,
            is_refunded=is_refunded,
            min_points=min_points,
            max_points=max_points,
            prompt_search=prompt_search,
            start_time=start_time,
            end_time=end_time,
        )

        # 北京日历日(UTC+8)。SQLite datetime 修饰符;列是 naive UTC 字符串。
        day_expr = func.strftime("%Y-%m-%d", col(cls.created_at), "+8 hours")
        failed_v = RHComfyuiTaskStatus.FAILED.value
        cancelled_v = RHComfyuiTaskStatus.CANCELLED.value
        stmt = select(
            day_expr.label("day"),
            func.count().label("requests"),
            func.coalesce(
                func.sum(
                    case(
                        (col(cls.status).in_((failed_v, cancelled_v)), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("failed"),
            func.coalesce(func.sum(col(cls.point_cost)), 0).label("points"),
            func.count(func.distinct(col(cls.user_id))).label("users"),
        ).select_from(cls)
        if conds:
            stmt = stmt.where(and_(*conds))
        stmt = stmt.group_by(day_expr).order_by(day_expr)
        rows = (await session.execute(stmt)).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            day = str(r.day or "").strip()
            if not day:
                continue
            out.append(
                {
                    "date": day,
                    "requests": int(r.requests or 0),
                    "failed": int(r.failed or 0),
                    "points": int(r.points or 0),
                    "users": int(r.users or 0),
                }
            )
        return out

    @classmethod
    @with_read_session
    async def get_provider_summaries(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """按供应商(backend_provider)聚合对账摘要,供 `供应商统计` 命令使用

        返回按总积分倒序:
          {
            "provider": str,
            "total": int,
            "success": int,
            "failed": int,
            "success_rate": float,
            "avg_elapsed_ms": int,
            "total_points": int,
          }
        backend_provider 为空的历史记录(单通道时代)不计入。
        禁止 ``select(cls).subquery()`` 拖入大 JSON 列。
        """
        conds: list[ColumnElement[bool]] = [col(cls.backend_provider) != ""]
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)
        ok_v = RHComfyuiTaskStatus.OK.value
        failed_v = RHComfyuiTaskStatus.FAILED.value
        cancelled_v = RHComfyuiTaskStatus.CANCELLED.value
        agg_stmt = (
            select(
                col(cls.backend_provider).label("provider"),
                func.count().label("total"),
                func.coalesce(func.avg(col(cls.elapsed_ms)), 0).label("avg_elapsed_ms"),
                func.coalesce(func.sum(col(cls.point_cost)), 0).label("total_points"),
                func.coalesce(
                    func.sum(case((col(cls.status) == ok_v, 1), else_=0)),
                    0,
                ).label("success"),
                func.coalesce(
                    func.sum(
                        case(
                            (col(cls.status).in_((failed_v, cancelled_v)), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("failed"),
            )
            .select_from(cls)
            .where(and_(*conds))
            .group_by(col(cls.backend_provider))
            .order_by(func.sum(col(cls.point_cost)).desc())
        )
        agg_rows = (await session.execute(agg_stmt)).all()

        results: list[dict[str, Any]] = []
        for r in agg_rows:
            total_i = int(r.total or 0)
            success = int(r.success or 0)
            failed = int(r.failed or 0)
            terminal = success + failed
            results.append(
                {
                    "provider": str(r.provider),
                    "total": total_i,
                    "success": success,
                    "failed": failed,
                    "success_rate": round(success / terminal, 4) if terminal else 0.0,
                    "avg_elapsed_ms": int(float(r.avg_elapsed_ms or 0)),
                    "total_points": int(r.total_points or 0),
                }
            )
        return results

    @classmethod
    @with_session
    async def mark_last_failed_refunded(
        cls,
        session: AsyncSession,
        user_id: str,
        bot_id: str,
        task_name: str,
    ) -> bool:
        """最近一条 failed/cancelled 且未退记录 → refunded=True(PointsBillingPolicy)。"""
        terminal = (
            RHComfyuiTaskStatus.FAILED.value,
            RHComfyuiTaskStatus.CANCELLED.value,
        )
        stmt = (
            select(cls)
            .where(
                and_(
                    col(cls.user_id) == user_id,
                    col(cls.bot_id) == bot_id,
                    col(cls.task_name) == task_name,
                    col(cls.status).in_(terminal),
                    col(cls.refunded) == False,  # noqa: E712
                )
            )
            .order_by(col(cls.created_at).desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        row.refunded = True
        session.add(row)
        return True

    @classmethod
    @with_session
    async def mark_host_wallet_refunded(
        cls,
        session: AsyncSession,
        *,
        trace_id: str = "",
        record_id: Optional[int] = None,
        terminal_status: Optional[str] = None,
    ) -> bool:
        """宿主 ExternalPrepaid 退钱包后回写消费表(按 record_id 或 trace_id)。

        - 设置 refunded=True
        - 若 terminal_status 给定且当前为 running,同步写终态(cancelled/failed)
        - 已 ok 的行不改(防迟到写回)
        """
        row: Optional[RHComfyuiTaskRecord] = None
        if record_id is not None and int(record_id) > 0:
            stmt = select(cls).where(col(cls.id) == int(record_id))
            row = (await session.execute(stmt)).scalar_one_or_none()
        tid = (trace_id or "").strip()
        if row is None and tid:
            stmt = select(cls).where(col(cls.trace_id) == tid).order_by(col(cls.created_at).desc()).limit(1)
            row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        if row.status == RHComfyuiTaskStatus.OK.value:
            return False
        if terminal_status and row.status == RHComfyuiTaskStatus.RUNNING.value:
            row.status = str(terminal_status)[:16]
        row.refunded = True
        session.add(row)
        return True

    @classmethod
    @with_session
    async def insert_task_record(
        cls,
        session: AsyncSession,
        *,
        user_id: str,
        bot_id: str,
        group_id: str,
        task_type: str,
        task_name: str,
        backend: str,
        backend_model: str,
        backend_provider: str,
        backend_key_prefix: str,
        duration_seconds: Optional[int],
        width: Optional[int],
        height: Optional[int],
        ratio: str,
        resolution: str,
        seed: Optional[int],
        voice_id: str,
        extra_params_json: str,
        prompt: str,
        status: str,
        elapsed_ms: Optional[int],
        point_cost: int,
        error_message: str,
        raw_response_json: str,
        trace_id: str,
        created_at: datetime,
        entry_point: str = "",
        saved_files_json: str = "",
        request_body_json: str = "",
    ) -> int:
        """插入一条任务执行记录;返回新行 id。供 statistics.record_task 调用。

        作为 classmethod + @with_session,以便基于 GsCore 的统一 session
        管理(自动重试 + commit + 异步上下文)。与本类其它 CRUD 方法签名一致:
        第一个位置参数是 session,其余全部 keyword-only。
        """
        record = cls(
            user_id=user_id,
            bot_id=bot_id,
            group_id=group_id,
            task_type=task_type,
            task_name=task_name,
            backend=backend,
            backend_model=backend_model,
            backend_provider=backend_provider,
            backend_key_prefix=backend_key_prefix,
            duration_seconds=duration_seconds,
            width=width,
            height=height,
            ratio=ratio,
            resolution=resolution,
            seed=seed,
            voice_id=voice_id,
            extra_params_json=extra_params_json,
            prompt=prompt,
            status=status,
            elapsed_ms=elapsed_ms,
            point_cost=point_cost,
            error_message=error_message,
            raw_response_json=raw_response_json,
            request_body_json=request_body_json,
            trace_id=trace_id,
            created_at=created_at,
            entry_point=entry_point,
            saved_files_json=saved_files_json,
        )
        session.add(record)
        await session.flush()  # 获取 id
        return int(record.id or 0)

    @classmethod
    @with_session
    async def update_task_record(
        cls,
        session: AsyncSession,
        record_id: int,
        *,
        status: str,
        elapsed_ms: Optional[int] = None,
        error_message: str = "",
        raw_response_json: str = "",
        saved_files_json: str = "",
        backend: str = "",
        backend_model: str = "",
        backend_provider: str = "",
        backend_key_prefix: str = "",
        point_cost: Optional[int] = None,
        extra_params_json: Optional[str] = None,
        request_body_json: Optional[str] = None,
        prompt: Optional[str] = None,
        refunded: Optional[bool] = None,
    ) -> bool:
        """按主键更新任务记录(执行中 → 终态)。找不到行返回 False。"""
        if record_id <= 0:
            return False
        stmt = select(cls).where(col(cls.id) == record_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        row.status = status[:16]
        if elapsed_ms is not None:
            row.elapsed_ms = elapsed_ms
        if error_message:
            row.error_message = error_message
        if raw_response_json:
            row.raw_response_json = raw_response_json
        if saved_files_json:
            row.saved_files_json = saved_files_json
        if backend:
            row.backend = backend[:32]
        if backend_model:
            row.backend_model = backend_model[:128]
        if backend_provider:
            row.backend_provider = backend_provider[:32]
        if backend_key_prefix:
            row.backend_key_prefix = backend_key_prefix[:16]
        if point_cost is not None:
            row.point_cost = int(point_cost)
        if extra_params_json is not None:
            row.extra_params_json = extra_params_json
        if request_body_json is not None:
            row.request_body_json = request_body_json
        if prompt is not None:
            row.prompt = str(prompt)[:4000]
        if refunded is not None:
            row.refunded = bool(refunded)
        session.add(row)
        return True

    @classmethod
    @with_session
    async def cas_update_point_cost(
        cls,
        session: AsyncSession,
        record_id: int,
        *,
        expected: int,
        new_cost: int,
    ) -> bool:
        """仅当当前 point_cost 仍是 expected 时写入实扣。供用量回算。

        走 ORM + with_session 提交,不依赖 SQLite UPDATE rowcount
        (aiosqlite 常返回 -1/0,会被误判失败)。
        """
        if record_id <= 0:
            return False
        stmt = select(cls).where(col(cls.id) == int(record_id))
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        if (row.status or "").strip().lower() != "ok":
            return False
        if int(row.point_cost or 0) != int(expected):
            return False
        row.point_cost = int(new_cost)
        extra: dict[str, Any] = {}
        raw_extra = (row.extra_params_json or "").strip()
        import json

        if raw_extra:
            try:
                parsed = json.loads(raw_extra)
                if isinstance(parsed, dict):
                    extra = parsed
            except (TypeError, ValueError):
                extra = {}
        extra["usage_settled_points"] = int(new_cost)
        row.extra_params_json = json.dumps(extra, ensure_ascii=False)
        session.add(row)
        return True

    @classmethod
    async def cleanup_old_records(cls, keep_days: int = 90) -> int:
        """清理超过 keep_days 天的记录;返回删除条数(分批,避免长事务)"""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
        total_deleted = 0
        try:
            while True:
                deleted = await cls._delete_batch(cutoff, batch=1000)
                total_deleted += deleted
                if deleted < 1000:
                    break
            if total_deleted:
                logger.info(f"[RHComfyUI.Statistics] 清理 {total_deleted} 条 {keep_days} 天前的任务记录")
            return total_deleted
        except Exception as e:
            # 兜底日志:清理失败不能影响系统运行,直接吞掉
            logger.warning(f"[RHComfyUI.Statistics] 清理失败(已忽略): {e}")
            return total_deleted

    @classmethod
    @with_session
    async def _delete_batch(
        cls,
        session: AsyncSession,
        cutoff: datetime,
        batch: int,
    ) -> int:
        stmt = delete(cls).where(col(cls.created_at) < cutoff).execution_options(synchronize_session="fetch")
        result = await session.execute(stmt)
        # CursorResult 类型守卫:session.execute() 静态类型不带 rowcount
        if isinstance(result, CursorResult):
            return int(result.rowcount or 0)
        return 0


@site.register_admin
class RHComfyuiTaskRecordAdmin(GsAdminModel):
    """WebConsole 后台查看任务统计记录"""

    pk_name = "id"
    page_schema = PageSchema(
        label="RH_ComfyUI 任务统计",
        icon="fa fa-line-chart",
    )  # type: ignore
    model = RHComfyuiTaskRecord


# ═══════════════════════════════════════════════════════════════════════
#  语音音色克隆缓存表
#
#  部分 TTS 上游支持"参考音频 → 克隆音色 id"。同一段参考音频反复提交时,
#  按内容哈希全局去重复用已克隆的音色 id,避免重复克隆(省额度 + 跨重启持久)。
#  克隆是纯内容派生(同音频→同音色),故全局共享、不按用户隔离;created_by 仅审计。
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
#  消费统计表缓存(admin /stats summary + TOP 用户)
#  写路径(insert/update/refund)调用 invalidate_stats_cache 清相关 bot_id
# ═══════════════════════════════════════════════════════════════════════


class RHComfyuiStatsCache(SQLModel, table=True):
    """admin 消费统计结果缓存表。

    - cache_key: 业务键(含 bot_id / 时间窗 / kind / top_n 等)
    - payload_json: JSON 可序列化结果
    - expires_at: unix 秒,过期行可惰性忽略并由下次写入覆盖
    """

    # SQLModel/SQLAlchemy 对 __tablename__ 的 stub 与字面量赋值冲突
    __tablename__ = "rhcomfyuistatscache"  # type: ignore[assignment]
    __table_args__ = (
        Index("ix_rhcomfyuistatscache_bot_exp", "bot_id", "expires_at"),
        {"extend_existing": True},
    )

    cache_key: str = Field(primary_key=True, max_length=256, title="缓存键")
    bot_id: str = Field(default="", index=True, max_length=64, title="Bot 平台")
    kind: str = Field(default="", max_length=32, title="summary|user_summaries")
    payload_json: str = Field(default="", title="JSON 载荷")
    expires_at: int = Field(default=0, title="过期 unix 秒")
    updated_at: int = Field(default=0, title="更新 unix 秒")

    @classmethod
    @with_read_session
    async def get_valid(
        cls,
        session: AsyncSession,
        cache_key: str,
        *,
        now: Optional[int] = None,
    ) -> Optional[str]:
        """命中且未过期返回 payload_json,否则 None。"""
        import time as _time

        ts = int(now if now is not None else _time.time())
        stmt = select(cls).where(col(cls.cache_key) == cache_key).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        if int(row.expires_at or 0) <= ts:
            return None
        return row.payload_json or None

    @classmethod
    @with_session
    async def upsert(
        cls,
        session: AsyncSession,
        *,
        cache_key: str,
        bot_id: str,
        kind: str,
        payload_json: str,
        ttl_seconds: int,
        now: Optional[int] = None,
    ) -> None:
        """写入/覆盖一条缓存。"""
        import time as _time

        ts = int(now if now is not None else _time.time())
        ttl = max(1, int(ttl_seconds))
        exp = ts + ttl
        stmt = select(cls).where(col(cls.cache_key) == cache_key).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            session.add(
                cls(
                    cache_key=cache_key[:256],
                    bot_id=(bot_id or "")[:64],
                    kind=(kind or "")[:32],
                    payload_json=payload_json,
                    expires_at=exp,
                    updated_at=ts,
                )
            )
        else:
            row.bot_id = (bot_id or "")[:64]
            row.kind = (kind or "")[:32]
            row.payload_json = payload_json
            row.expires_at = exp
            row.updated_at = ts
            session.add(row)

    @classmethod
    @with_session
    async def invalidate(
        cls,
        session: AsyncSession,
        bot_id: Optional[str] = None,
    ) -> int:
        """失效缓存。bot_id 为空则清空全表;否则只清该 bot 相关行。返回删除行数。"""
        if bot_id:
            stmt = delete(cls).where(col(cls.bot_id) == bot_id)
        else:
            stmt = delete(cls)
        result = await session.execute(stmt)
        # CursorResult.rowcount 在部分驱动上可能是 -1
        try:
            return int(getattr(result, "rowcount", 0) or 0)
        except Exception:
            return 0


@site.register_admin
class RHComfyuiStatsCacheAdmin(GsAdminModel):
    """WebConsole 查看/清理消费统计缓存"""

    pk_name = "cache_key"
    page_schema = PageSchema(
        label="RH_ComfyUI 消费统计缓存",
        icon="fa fa-database",
    )  # type: ignore
    model = RHComfyuiStatsCache


class RHVoiceCloneCache(SQLModel, table=True):
    """参考音频哈希 → 已克隆音色 id 的持久映射(全局去重)"""

    __table_args__ = {"extend_existing": True}

    id: Optional[int] = Field(default=None, primary_key=True, title="序号")
    provider: str = Field(default="", title="上游后端名", index=True, max_length=32)
    audio_hash: str = Field(title="参考音频内容哈希", index=True, max_length=64)
    voice_model_id: str = Field(title="上游返回的音色 id", max_length=128)
    title: str = Field(default="", title="音色标题", max_length=128)
    created_by: str = Field(default="", title="首次创建者(审计)", max_length=64)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        title="创建时间 UTC",
    )

    @classmethod
    @with_session
    async def get_voice_id(
        cls,
        session: AsyncSession,
        provider: str,
        audio_hash: str,
    ) -> Optional[str]:
        """命中返回已克隆音色 id,未命中返回 None"""
        stmt = select(cls).where(and_(col(cls.provider) == provider, col(cls.audio_hash) == audio_hash)).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        return row.voice_model_id if row is not None else None

    @classmethod
    @with_session
    async def remember(
        cls,
        session: AsyncSession,
        *,
        provider: str,
        audio_hash: str,
        voice_model_id: str,
        title: str = "",
        created_by: str = "",
    ) -> None:
        """记住一条映射;并发下若已存在则跳过,避免重复行"""
        stmt = select(cls).where(and_(col(cls.provider) == provider, col(cls.audio_hash) == audio_hash)).limit(1)
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            return
        session.add(
            cls(
                provider=provider,
                audio_hash=audio_hash,
                voice_model_id=voice_model_id,
                title=title,
                created_by=created_by,
            )
        )


@site.register_admin
class RHVoiceCloneCacheAdmin(GsAdminModel):
    """WebConsole 后台查看/清理音色克隆缓存"""

    pk_name = "id"
    page_schema = PageSchema(
        label="RH_ComfyUI 音色克隆缓存",
        icon="fa fa-microphone",
    )  # type: ignore
    model = RHVoiceCloneCache


exec_list.extend(
    [
        'ALTER TABLE rhcomfyuitaskrecord ADD COLUMN prompt TEXT DEFAULT ""',
        'ALTER TABLE rhcomfyuitaskrecord ADD COLUMN entry_point VARCHAR(16) DEFAULT ""',
        'ALTER TABLE rhcomfyuitaskrecord ADD COLUMN backend_key_prefix VARCHAR(16) DEFAULT ""',
        'ALTER TABLE rhcomfyuitaskrecord ADD COLUMN saved_files_json TEXT DEFAULT ""',
        'ALTER TABLE rhcomfyuitaskrecord ADD COLUMN request_body_json TEXT DEFAULT ""',
        # 三重余额(5h/日/周);旧行戳为 0 时 ensure_refreshed 会迁移初始化
        "ALTER TABLE rhbind ADD COLUMN point_5h INTEGER DEFAULT 0",
        "ALTER TABLE rhbind ADD COLUMN point_day INTEGER DEFAULT 0",
        "ALTER TABLE rhbind ADD COLUMN point_week INTEGER DEFAULT 0",
        "ALTER TABLE rhbind ADD COLUMN refreshed_at_5h INTEGER DEFAULT 0",
        "ALTER TABLE rhbind ADD COLUMN refreshed_at_day INTEGER DEFAULT 0",
        "ALTER TABLE rhbind ADD COLUMN refreshed_at_week INTEGER DEFAULT 0",
        'ALTER TABLE rhbind ADD COLUMN vip_tier VARCHAR(16) DEFAULT "free"',
        # ── admin stats 复合索引(与 SQLModel __table_args__ 对齐;旧库启动补齐) ──
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_created ON rhcomfyuitaskrecord (bot_id, created_at)",
        # 同名 covering 加了 elapsed_ms/task_type;旧 5 列索引要先 DROP 再建
        "DROP INDEX IF EXISTS ix_rhcomfyuitaskrecord_bot_created_user_spend",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_created_user_spend "
        "ON rhcomfyuitaskrecord (bot_id, created_at, user_id, status, point_cost, elapsed_ms, task_type)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_user ON rhcomfyuitaskrecord (bot_id, user_id)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_user_created "
        "ON rhcomfyuitaskrecord (bot_id, user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_status_created "
        "ON rhcomfyuitaskrecord (bot_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_user_created ON rhcomfyuitaskrecord (user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_pipeline "
        "ON rhcomfyuitaskrecord (bot_id, task_name, task_type)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_bmodel ON rhcomfyuitaskrecord (bot_id, backend_model)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_bmodel_type "
        "ON rhcomfyuitaskrecord (bot_id, backend_model, task_type)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_backend ON rhcomfyuitaskrecord (bot_id, backend)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_trace_id ON rhcomfyuitaskrecord (trace_id)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_trace ON rhcomfyuitaskrecord (bot_id, trace_id)",
        # 统计缓存表索引(表由 create_all 建出后补索引)
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuistatscache_bot_exp ON rhcomfyuistatscache (bot_id, expires_at)",
    ]
)
