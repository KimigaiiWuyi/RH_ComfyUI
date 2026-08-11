from enum import Enum
from typing import Any, Optional, TypedDict
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, col
from sqlalchemy import Index, ColumnElement, and_, case, func, delete, select
from sqlalchemy.orm import defer
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.utils.database.base_models import Bind, with_session

from ..core.request import TaskType
from ...rh_config.comfyui_config import PLUGIN_CONFIG


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
    vip_tier: str = Field(default="free", title="额度档 free/basic/pro/enterprise", max_length=16)

    # ── 内部:三桶读写 ────────────────────────────────────────────

    @classmethod
    def _bucket_vals(cls, row: "RHBind") -> tuple[int, int, int]:
        return (
            int(getattr(row, "point_5h", 0) or 0),
            int(getattr(row, "point_day", 0) or 0),
            int(getattr(row, "point_week", 0) or 0),
        )

    @classmethod
    def _sync_available(cls, h5: int, day: int, week: int) -> int:
        return max(0, min(int(h5), int(day), int(week)))

    @classmethod
    async def _persist_buckets(
        cls,
        user_id: str,
        bot_id: str,
        *,
        h5: int,
        day: int,
        week: int,
        refreshed_at_5h: int,
        refreshed_at_day: int,
        refreshed_at_week: int,
        vip_tier: str = "free",
    ) -> int:
        available = cls._sync_available(h5, day, week)
        await cls.update_data(
            user_id=user_id,
            bot_id=bot_id,
            point=available,
            point_5h=int(h5),
            point_day=int(day),
            point_week=int(week),
            refreshed_at_5h=int(refreshed_at_5h),
            refreshed_at_day=int(refreshed_at_day),
            refreshed_at_week=int(refreshed_at_week),
            vip_tier=(vip_tier or "free")[:16],
        )
        return available

    @classmethod
    async def ensure_refreshed(
        cls,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: Optional[str] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """懒刷新三桶到档位满额,返回 status dict(含 available)。

        档位优先级(与 bot_id 无关):
          1. 显式 ``vip_tier`` 参数
          2. 行上已存 ``RHBind.vip_tier``
          3. free

        force=True 时无视时间戳,三桶立即补满(管理端/手动刷新);5h 计时清零。

        5h 计时规则:
          - 补满后 timer=0,闲置不计时
          - 首次扣费时写入 timer=now
          - timer 起经过窗口秒数后自动补满并 timer=0
        """
        from ...core.billing.tier_quota import (
            now_ts,
            normalize_tier,
            get_tier_quotas,
            needs_5h_refresh,
            needs_day_refresh,
            needs_week_refresh,
            start_of_local_day,
            start_of_local_week,
        )

        n = now_ts()
        row = await cls.select_data(user_id=user_id, bot_id=bot_id)

        if vip_tier is not None:
            tier = normalize_tier(vip_tier)
        elif row is not None:
            tier = normalize_tier(getattr(row, "vip_tier", None))
        else:
            tier = "free"

        quotas = get_tier_quotas(tier)

        if row is None:
            await cls.create_data(
                user_id=user_id,
                bot_id=bot_id,
                vip_tier=tier,
            )
            row = await cls.select_data(user_id=user_id, bot_id=bot_id)
            assert row is not None

        h5, day, week = cls._bucket_vals(row)
        # r5 = 5h 计时起点(0=未开始)
        r5 = int(getattr(row, "refreshed_at_5h", 0) or 0)
        rd = int(getattr(row, "refreshed_at_day", 0) or 0)
        rw = int(getattr(row, "refreshed_at_week", 0) or 0)
        old_point = int(getattr(row, "point", 0) or 0)

        # 旧行迁移:日/周戳全 0 → 初始化;5h 满额且 timer=0(闲置)
        if rd <= 0 and rw <= 0 and h5 <= 0 and day <= 0 and week <= 0:
            h5 = quotas.h5
            day = max(old_point, quotas.day)
            week = quotas.week
            r5 = 0  # 满额未用,不计时
            rd = start_of_local_day(n)
            rw = start_of_local_week(n)
            available = await cls._persist_buckets(
                user_id,
                bot_id,
                h5=h5,
                day=day,
                week=week,
                refreshed_at_5h=r5,
                refreshed_at_day=rd,
                refreshed_at_week=rw,
                vip_tier=tier,
            )
            return cls._status_dict(
                available=available,
                h5=h5,
                day=day,
                week=week,
                quotas=quotas,
                r5=r5,
                rd=rd,
                rw=rw,
                tier=tier,
            )

        # 已满额但还挂着旧版滚动计时 → 清零计时(符合「满额闲置不计时」)
        if h5 >= quotas.h5 and r5 > 0 and not force:
            r5 = 0
            changed_idle = True
        else:
            changed_idle = False

        changed = changed_idle
        if force or needs_5h_refresh(r5, n):
            h5 = quotas.h5
            r5 = 0  # 补满后重新闲置,等下次首次消费再计时
            changed = True
        if force or needs_day_refresh(rd, n):
            day = quotas.day
            rd = start_of_local_day(n)
            changed = True
        if force or needs_week_refresh(rw, n):
            week = quotas.week
            rw = start_of_local_week(n)
            changed = True

        # 档位变更时仍保持余额,但 stamp vip_tier
        stored_tier = str(getattr(row, "vip_tier", "") or "free")
        if stored_tier != tier:
            changed = True

        if changed:
            available = await cls._persist_buckets(
                user_id,
                bot_id,
                h5=h5,
                day=day,
                week=week,
                refreshed_at_5h=r5,
                refreshed_at_day=rd,
                refreshed_at_week=rw,
                vip_tier=tier,
            )
        else:
            available = cls._sync_available(h5, day, week)
            # 兼容字段漂移时纠偏
            if int(getattr(row, "point", 0) or 0) != available:
                await cls.update_data(user_id=user_id, bot_id=bot_id, point=available)

        return cls._status_dict(
            available=available,
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
    def _status_dict(
        cls,
        *,
        available: int,
        h5: int,
        day: int,
        week: int,
        quotas: Any,
        r5: int,
        rd: int,
        rw: int,
        tier: str,
    ) -> dict[str, Any]:
        from ...core.billing.tier_quota import (
            next_5h_refresh_at,
            next_day_refresh_at,
            next_week_refresh_at,
        )

        return {
            "available": int(available),
            "point": int(available),
            "tier": tier,
            "label": getattr(quotas, "label", tier),
            "buckets": {
                "h5": {
                    "balance": int(h5),
                    "cap": int(quotas.h5),
                    # 0 = 未开始计时(满额闲置);>0 = 预计补满 unix
                    "next_refresh_at": next_5h_refresh_at(r5),
                    "timer_started_at": int(r5),
                    "timer_active": bool(r5 > 0),
                },
                "day": {
                    "balance": int(day),
                    "cap": int(quotas.day),
                    "next_refresh_at": next_day_refresh_at(),
                },
                "week": {
                    "balance": int(week),
                    "cap": int(quotas.week),
                    "next_refresh_at": next_week_refresh_at(),
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
    ) -> dict[str, Any]:
        return await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier)

    @classmethod
    async def deduct_triple(
        cls,
        user_id: str,
        bot_id: str,
        amount: int,
        *,
        vip_tier: Optional[str] = None,
    ) -> tuple[bool, dict[str, Any]]:
        """三桶同扣。返回 (ok, status_or_error_detail)。"""
        if amount <= 0:
            st = await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier)
            return True, st

        st = await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier)
        h5 = int(st["buckets"]["h5"]["balance"])
        day = int(st["buckets"]["day"]["balance"])
        week = int(st["buckets"]["week"]["balance"])
        if h5 < amount or day < amount or week < amount:
            short = []
            if h5 < amount:
                short.append(f"5小时额度不足(剩{h5})")
            if day < amount:
                short.append(f"今日额度不足(剩{day})")
            if week < amount:
                short.append(f"本周额度不足(剩{week})")
            detail = {
                **st,
                "ok": False,
                "need": amount,
                "reason": "；".join(short),
                "short_buckets": [b for b, bal in (("h5", h5), ("day", day), ("week", week)) if bal < amount],
            }
            logger.warning(
                f"[RHBind.deduct_triple] 不足 user={user_id} bot_id={bot_id} "
                f"need={amount} h5={h5} day={day} week={week}"
            )
            return False, detail

        h5 -= amount
        day -= amount
        week -= amount
        tier = str(st.get("tier") or "free")
        # 5h:满额闲置(timer=0)时首次消费 → 启动计时
        from ...core.billing.tier_quota import now_ts, get_tier_quotas

        r5 = int(st["refreshed_at"]["h5"])
        if r5 <= 0:
            r5 = now_ts()
        rd = int(st["refreshed_at"]["day"])
        rw = int(st["refreshed_at"]["week"])
        available = await cls._persist_buckets(
            user_id,
            bot_id,
            h5=h5,
            day=day,
            week=week,
            refreshed_at_5h=r5,
            refreshed_at_day=rd,
            refreshed_at_week=rw,
            vip_tier=tier,
        )

        out = cls._status_dict(
            available=available,
            h5=h5,
            day=day,
            week=week,
            quotas=get_tier_quotas(tier),
            r5=r5,
            rd=rd,
            rw=rw,
            tier=tier,
        )
        logger.info(
            f"[RHBind.deduct_triple] ok user={user_id} bot_id={bot_id} -{amount} "
            f"→ avail={available} (h5={h5},day={day},week={week})"
        )
        return True, out

    @classmethod
    async def add_triple(
        cls,
        user_id: str,
        bot_id: str,
        amount: int,
        *,
        vip_tier: Optional[str] = None,
        cap_to_tier: bool = True,
    ) -> dict[str, Any]:
        """三桶同加;默认不超过档位 cap(退款场景)。"""
        from ...core.billing.tier_quota import normalize_tier, get_tier_quotas

        if amount <= 0:
            return await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier)

        # 退款不触发周期刷新,避免「失败退款却顺带把桶补满」
        row = await cls.select_data(user_id=user_id, bot_id=bot_id)
        if vip_tier is not None:
            tier = normalize_tier(vip_tier)
        elif row is not None:
            tier = normalize_tier(getattr(row, "vip_tier", None))
        else:
            tier = "free"
        quotas = get_tier_quotas(tier)
        if row is None:
            await cls.create_data(user_id=user_id, bot_id=bot_id, vip_tier=tier)
            row = await cls.select_data(user_id=user_id, bot_id=bot_id)
            assert row is not None

        h5, day, week = cls._bucket_vals(row)
        h5 += amount
        day += amount
        week += amount
        if cap_to_tier:
            h5 = min(h5, quotas.h5)
            day = min(day, quotas.day)
            week = min(week, quotas.week)

        r5 = int(getattr(row, "refreshed_at_5h", 0) or 0)
        rd = int(getattr(row, "refreshed_at_day", 0) or 0)
        rw = int(getattr(row, "refreshed_at_week", 0) or 0)
        available = await cls._persist_buckets(
            user_id,
            bot_id,
            h5=h5,
            day=day,
            week=week,
            refreshed_at_5h=r5,
            refreshed_at_day=rd,
            refreshed_at_week=rw,
            vip_tier=tier,
        )
        logger.info(
            f"[RHBind.add_triple] user={user_id} bot_id={bot_id} +{amount} "
            f"→ avail={available} (h5={h5},day={day},week={week})"
        )
        return cls._status_dict(
            available=available,
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
    async def force_refill(
        cls,
        user_id: str,
        bot_id: str,
        *,
        vip_tier: Optional[str] = None,
    ) -> dict[str, Any]:
        """管理端:立刻把三桶补到当前档满额。"""
        return await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier, force=True)

    @classmethod
    async def refill_buckets(
        cls,
        user_id: str,
        bot_id: str,
        buckets: list[str] | tuple[str, ...] | str = "all",
        *,
        vip_tier: Optional[str] = None,
    ) -> dict[str, Any]:
        """管理端:只补满指定桶(h5 / day / week / all)。

        补满 5h 时会清零 5h 计时(回到闲置)。
        """
        from ...core.billing.tier_quota import (
            now_ts,
            normalize_tier,
            get_tier_quotas,
            start_of_local_day,
            start_of_local_week,
        )

        st = await cls.ensure_refreshed(user_id, bot_id, vip_tier=vip_tier, force=False)
        tier = normalize_tier(vip_tier or st.get("tier"))
        quotas = get_tier_quotas(tier)
        n = now_ts()

        if isinstance(buckets, str):
            keys = ["h5", "day", "week"] if buckets in ("all", "*") else [buckets]
        else:
            keys = list(buckets)
        keys = [k.strip().lower() for k in keys if k]
        valid = {"h5", "day", "week"}
        keys = [k for k in keys if k in valid]
        if not keys:
            return st

        h5 = int(st["buckets"]["h5"]["balance"])
        day = int(st["buckets"]["day"]["balance"])
        week = int(st["buckets"]["week"]["balance"])
        r5 = int(st["refreshed_at"]["h5"])
        rd = int(st["refreshed_at"]["day"])
        rw = int(st["refreshed_at"]["week"])

        if "h5" in keys:
            h5 = quotas.h5
            r5 = 0
        if "day" in keys:
            day = quotas.day
            rd = start_of_local_day(n)
        if "week" in keys:
            week = quotas.week
            rw = start_of_local_week(n)

        available = await cls._persist_buckets(
            user_id,
            bot_id,
            h5=h5,
            day=day,
            week=week,
            refreshed_at_5h=r5,
            refreshed_at_day=rd,
            refreshed_at_week=rw,
            vip_tier=tier,
        )
        return cls._status_dict(
            available=available,
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
    async def set_vip_tier(
        cls,
        user_id: str,
        bot_id: str,
        tier: str,
        *,
        refill: bool = True,
    ) -> dict[str, Any]:
        """设置该 (user_id, bot_id) 池的额度档;默认立即按新档三桶补满。

        与 bot_id 平台无关 — qq / canvas / 其它一律可设 basic/pro/enterprise。
        """
        from ...core.billing.tier_quota import normalize_tier

        t = normalize_tier(tier)
        row = await cls.select_data(user_id=user_id, bot_id=bot_id)
        if row is None:
            await cls.create_data(user_id=user_id, bot_id=bot_id, vip_tier=t)
        else:
            await cls.update_data(user_id=user_id, bot_id=bot_id, vip_tier=t[:16])
        if refill:
            return await cls.ensure_refreshed(user_id, bot_id, vip_tier=t, force=True)
        return await cls.ensure_refreshed(user_id, bot_id, vip_tier=t, force=False)

    # ── 兼容旧 API ───────────────────────────────────────────────

    @classmethod
    async def create_data(
        cls,
        user_id: str,
        bot_id: str,
        point: Optional[int] = None,
        *,
        vip_tier: str = "free",
    ):
        from ...core.billing.tier_quota import (
            now_ts,
            normalize_tier,
            get_tier_quotas,
            start_of_local_day,
            start_of_local_week,
        )

        tier = normalize_tier(vip_tier)
        quotas = get_tier_quotas(tier)
        n = now_ts()
        # point 参数仅作兼容:若显式传入,日桶至少这么多
        day = quotas.day
        if point is not None:
            day = max(int(point), quotas.day)
        h5, week = quotas.h5, quotas.week
        available = cls._sync_available(h5, day, week)
        # 新建满额:5h 计时=0(闲置,首次消费才启动)
        r5 = 0

        await cls.insert_data(
            group_id=None,
            user_id=user_id,
            bot_id=bot_id,
            point=available,
            point_5h=h5,
            point_day=day,
            point_week=week,
            refreshed_at_5h=r5,
            refreshed_at_day=start_of_local_day(n),
            refreshed_at_week=start_of_local_week(n),
            vip_tier=tier[:16],
        )
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            return cls(
                group_id=None,
                user_id=user_id,
                bot_id=bot_id,
                point=available,
                point_5h=h5,
                point_day=day,
                point_week=week,
                refreshed_at_5h=r5,
                refreshed_at_day=start_of_local_day(n),
                refreshed_at_week=start_of_local_week(n),
                vip_tier=tier[:16],
            )
        return bind_data

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
        Index("ix_rhcomfyuitaskrecord_bot_user", "bot_id", "user_id"),
        Index("ix_rhcomfyuitaskrecord_bot_status_created", "bot_id", "status", "created_at"),
        Index("ix_rhcomfyuitaskrecord_user_created", "user_id", "created_at"),
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
    @with_session
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
        if bot_id is not None:
            conds.append(col(cls.bot_id) == bot_id)
        if task_type is not None:
            conds.append(col(cls.task_type) == task_type)
        if task_name is not None:
            conds.append(col(cls.task_name) == task_name)
        if status is not None:
            conds.append(col(cls.status) == status)
        if trace_id is not None:
            conds.append(col(cls.trace_id) == trace_id)
        if backend is not None:
            conds.append(col(cls.backend) == backend)
        if is_refunded is not None:
            conds.append(col(cls.refunded) == is_refunded)
        if min_points is not None:
            conds.append(col(cls.point_cost) >= min_points)
        if max_points is not None:
            conds.append(col(cls.point_cost) <= max_points)
        if prompt_search is not None:
            # LIKE 模糊匹配(2026-07-01 prompt 列已加,见 exec_list)
            conds.append(col(cls.prompt).contains(prompt_search))
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)

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
    @with_session
    async def get_summary(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        bot_id: Optional[str] = None,
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
    @with_session
    async def list_all(
        cls,
        session: AsyncSession,
        user_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        group_id: Optional[str] = None,
        task_type: Optional[str] = None,
        task_name: Optional[str] = None,
        status: Optional[str] = None,
        trace_id: Optional[str] = None,
        backend: Optional[str] = None,
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
        """
        conds: list[ColumnElement[bool]] = []
        if user_id is not None:
            conds.append(col(cls.user_id) == user_id)
        if bot_id is not None:
            conds.append(col(cls.bot_id) == bot_id)
        if group_id is not None:
            conds.append(col(cls.group_id) == group_id)
        if task_type is not None:
            conds.append(col(cls.task_type) == task_type)
        if task_name is not None:
            conds.append(col(cls.task_name) == task_name)
        if status is not None:
            conds.append(col(cls.status) == status)
        if trace_id is not None:
            conds.append(col(cls.trace_id) == trace_id)
        if backend is not None:
            conds.append(col(cls.backend) == backend)
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

        stmt = select(cls).options(*cls._defer_heavy_columns())
        if conds:
            stmt = stmt.where(and_(*conds))
        stmt = stmt.order_by(col(cls.created_at).desc()).offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    @classmethod
    @with_session
    async def get_by_record_id(
        cls,
        session: AsyncSession,
        record_id: int,
    ) -> Optional["RHComfyuiTaskRecord"]:
        """按主键查询单条记录;不存在返回 None(供消费详情懒加载使用)"""
        stmt = select(cls).where(col(cls.id) == record_id).limit(1)
        return (await session.execute(stmt)).scalar_one_or_none()

    @classmethod
    @with_session
    async def get_user_summaries(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        top_n: int = 20,
        bot_id: Optional[str] = None,
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
    @with_session
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
        """
        conds: list[ColumnElement[bool]] = [col(cls.backend_provider) != ""]
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)
        base_sub = select(cls).where(and_(*conds)).subquery()

        agg_stmt = (
            select(
                base_sub.c.backend_provider.label("provider"),
                func.count().label("total"),
                func.coalesce(func.avg(base_sub.c.elapsed_ms), 0).label("avg_elapsed_ms"),
                func.coalesce(func.sum(base_sub.c.point_cost), 0).label("total_points"),
            )
            .group_by(base_sub.c.backend_provider)
            .order_by(func.sum(base_sub.c.point_cost).desc())
        )
        agg_rows = (await session.execute(agg_stmt)).all()

        success_stmt = (
            select(base_sub.c.backend_provider, func.count())
            .where(base_sub.c.status == RHComfyuiTaskStatus.OK.value)
            .group_by(base_sub.c.backend_provider)
        )
        success_map = {p: int(cnt) for p, cnt in (await session.execute(success_stmt)).all()}
        failed_stmt = (
            select(base_sub.c.backend_provider, func.count())
            .where(base_sub.c.status.in_((RHComfyuiTaskStatus.FAILED.value, RHComfyuiTaskStatus.CANCELLED.value)))
            .group_by(base_sub.c.backend_provider)
        )
        failed_map = {p: int(cnt) for p, cnt in (await session.execute(failed_stmt)).all()}

        results: list[dict[str, Any]] = []
        for provider, total, avg_elapsed, total_points in agg_rows:
            total_i = int(total)
            success = success_map.get(provider, 0)
            failed = failed_map.get(provider, 0)
            terminal = success + failed
            results.append(
                {
                    "provider": str(provider),
                    "total": total_i,
                    "success": success,
                    "failed": failed,
                    "success_rate": round(success / terminal, 4) if terminal else 0.0,
                    "avg_elapsed_ms": int(float(avg_elapsed or 0)),
                    "total_points": int(total_points),
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
    @with_session
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
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_user ON rhcomfyuitaskrecord (bot_id, user_id)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_bot_status_created "
        "ON rhcomfyuitaskrecord (bot_id, status, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuitaskrecord_user_created ON rhcomfyuitaskrecord (user_id, created_at)",
        # 统计缓存表索引(表由 create_all 建出后补索引)
        "CREATE INDEX IF NOT EXISTS ix_rhcomfyuistatscache_bot_exp ON rhcomfyuistatscache (bot_id, expires_at)",
    ]
)
