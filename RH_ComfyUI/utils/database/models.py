from enum import Enum
from typing import Any, Optional, TypedDict
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel, col
from sqlalchemy import ColumnElement, and_, func, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.utils.database.base_models import Bind, with_session

from ..core.request import TaskType
from ...rh_config.comfyui_config import PLUGIN_CONFIG


class TaskSummary(TypedDict):
    """RHComfyuiTaskRecord.get_summary() 的固定返回结构。"""

    total: int
    success: int
    failed: int
    success_rate: float
    avg_elapsed_ms: int
    by_task_type: dict[str, int]

DEFAULT_POINT: int = PLUGIN_CONFIG.get_config("Default_Point").data


class RHBind(Bind, table=True):
    __table_args__ = {"extend_existing": True}
    point: int = Field(default=20, title="积分")

    @classmethod
    async def create_data(
        cls,
        user_id: str,
        bot_id: str,
        point: Optional[int] = None,
    ):
        if point is None:
            point = DEFAULT_POINT

        await cls.insert_data(
            group_id=None,
            user_id=user_id,
            bot_id=bot_id,
            point=point,
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
                point=point,
            )
        return bind_data

    @classmethod
    async def add_point(
        cls,
        user_id: str,
        bot_id: str,
        add_point_num: int,
    ) -> int:
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            bind_data = await cls.create_data(
                user_id=user_id,
                bot_id=bot_id,
            )

        bind_data.point += add_point_num
        await cls.update_data(
            user_id=user_id,
            bot_id=bot_id,
            point=bind_data.point,
        )
        return 0

    @classmethod
    async def get_point(
        cls,
        user_id: str,
        bot_id: str,
    ) -> int:
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            return 0
        return bind_data.point

    @classmethod
    async def deduct_point(
        cls,
        user_id: str,
        bot_id: str,
        deduct_point_num: int,
        initial_point: int = DEFAULT_POINT,
    ) -> bool:
        """扣减积分;无记录时先建账(初始积分可选,默认 ``DEFAULT_POINT``)。"""
        bind_data = await cls.select_data(
            user_id=user_id,
            bot_id=bot_id,
        )
        if bind_data is None:
            logger.info(f"[RHBind.deduct_point] 建账 user={user_id} bot_id={bot_id} initial={initial_point}")
            bind_data = await cls.create_data(
                user_id=user_id,
                bot_id=bot_id,
                point=initial_point,
            )

        if bind_data.point < deduct_point_num:
            logger.warning(
                f"[RHBind.deduct_point] 积分不足 user={user_id} bot_id={bot_id}"
                f" current={bind_data.point} need={deduct_point_num}"
            )
            return False

        old_point = bind_data.point
        bind_data.point -= deduct_point_num
        await cls.update_data(
            user_id=user_id,
            bot_id=bot_id,
            point=bind_data.point,
        )
        logger.info(
            f"[RHBind.deduct_point] 扣减成功 user={user_id} bot_id={bot_id}"
            f" {old_point} -> {bind_data.point} (deducted {deduct_point_num})"
        )
        return True


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
#  每次 execute_generation() 完成(成功/失败)由 utils.database.statistics
#  模块函数 record_task() 写入一条。该模型同时承载 AI 工具与 WebConsole
#  后台的查询入口。
# ═══════════════════════════════════════════════════════════════════════


class RHComfyuiTaskStatus(str, Enum):
    """任务执行状态枚举"""

    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"  # 预留:支持取消时使用


class RHComfyuiTaskRecord(SQLModel, table=True):
    """RH_ComfyUI 任务执行统计记录"""

    __table_args__ = {"extend_existing": True}

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
    status: str = Field(title="状态 ok/failed/cancelled", index=True, max_length=16)
    elapsed_ms: Optional[int] = Field(default=None, title="任务耗时(毫秒)")
    point_cost: int = Field(default=0, title="本次消耗积分数")
    refunded: bool = Field(default=False, title="失败时是否已退回积分")
    error_message: str = Field(default="", title="失败摘要(截断到 2KB)")

    # ── 原始数据 ──
    raw_response_json: str = Field(default="", title="厂商原始响应 JSON(已截断到 64KB)")

    # ── 关联 ──
    trace_id: str = Field(default="", title="调用链追踪ID(由 GenerationRequest.trace_id 传入)", max_length=64)

    # ── 入口维度(2026-07-02 ABC 重构新增;旧记录为空串) ──
    entry_point: str = Field(default="", title="触发入口 command/agent/http", max_length=16)

    # ── 时间戳 ──
    # 索引单列 + 部分数据库可优化按 user_id 时间范围扫描的复合索引
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

        stmt = select(cls).where(and_(*conds)).order_by(col(cls.created_at).desc()).offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    @classmethod
    @with_session
    async def get_summary(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> TaskSummary:
        """聚合统计:总任务数 / 成功数 / 失败数 / 各类型计数 / 平均耗时"""
        conds: list[ColumnElement[bool]] = []
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)

        # 基础子查询统一带 WHERE,避免 4 条查询各自拼条件
        base = select(cls)
        if conds:
            base = base.where(and_(*conds))
        base_sub = base.subquery()

        total_rows = await session.execute(select(func.count()).select_from(base_sub))
        total = int(total_rows.scalar() or 0)

        success_rows = await session.execute(
            select(func.count()).select_from(base.where(col(cls.status) == RHComfyuiTaskStatus.OK.value).subquery())
        )
        success = int(success_rows.scalar() or 0)

        avg_rows = await session.execute(select(func.avg(col(cls.elapsed_ms))).select_from(base_sub))
        avg_elapsed = float(avg_rows.scalar() or 0)

        type_rows = await session.execute(
            select(col(cls.task_type), func.count()).select_from(base_sub).group_by(col(cls.task_type))
        )
        by_type_pairs = type_rows.all()
        return {
            "total": total,
            "success": success,
            "failed": total - success,
            "success_rate": round(success / total, 4) if total else 0.0,
            "avg_elapsed_ms": int(avg_elapsed),
            "by_task_type": {k: int(v) for k, v in by_type_pairs if k},
        }

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

        stmt = select(cls)
        if conds:
            stmt = stmt.where(and_(*conds))
        stmt = stmt.order_by(col(cls.created_at).desc()).offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    @classmethod
    @with_session
    async def get_user_summaries(
        cls,
        session: AsyncSession,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        top_n: int = 20,
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
        """
        conds: list[ColumnElement[bool]] = []
        if start_time is not None:
            conds.append(col(cls.created_at) >= start_time)
        if end_time is not None:
            conds.append(col(cls.created_at) <= end_time)

        base = select(cls)
        if conds:
            base = base.where(and_(*conds))
        base_sub = base.subquery()

        # 按 user_id 聚合:总条数 / 总积分
        agg_stmt = (
            select(
                base_sub.c.user_id.label("user_id"),
                func.count().label("total"),
                func.coalesce(func.sum(base_sub.c.point_cost), 0).label("total_points"),
            )
            .group_by(base_sub.c.user_id)
            .order_by(func.sum(base_sub.c.point_cost).desc())
            .limit(top_n)
        )
        agg_rows = (await session.execute(agg_stmt)).all()

        # 按 user_id + status 聚合:成功/失败计数
        success_stmt = (
            select(base_sub.c.user_id, func.count())
            .where(base_sub.c.status == RHComfyuiTaskStatus.OK.value)
            .group_by(base_sub.c.user_id)
        )
        success_map = {uid: int(cnt) for uid, cnt in (await session.execute(success_stmt)).all()}

        # 按 user_id + task_type 聚合:任务类型分布
        type_stmt = select(base_sub.c.user_id, base_sub.c.task_type, func.count()).group_by(
            base_sub.c.user_id, base_sub.c.task_type
        )
        type_pairs = (await session.execute(type_stmt)).all()

        results: list[dict[str, Any]] = []
        for uid, total, total_points in agg_rows:
            by_type: dict[str, int] = {}
            for row_uid, ttype, cnt in type_pairs:
                if row_uid == uid and ttype:
                    by_type[ttype] = int(cnt)
            results.append(
                {
                    "user_id": str(uid),
                    "total": int(total),
                    "success": success_map.get(uid, 0),
                    "failed": int(total) - success_map.get(uid, 0),
                    "total_points": int(total_points),
                    "by_task_type": by_type,
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

        results: list[dict[str, Any]] = []
        for provider, total, avg_elapsed, total_points in agg_rows:
            total_i = int(total)
            success = success_map.get(provider, 0)
            results.append(
                {
                    "provider": str(provider),
                    "total": total_i,
                    "success": success,
                    "failed": total_i - success,
                    "success_rate": round(success / total_i, 4) if total_i else 0.0,
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
        """把某用户最近一条匹配 (user, bot, task_name, status=failed, refunded=False)
        的记录标记为 refunded=True;供 _do_generate 退积分后调用"""
        stmt = (
            select(cls)
            .where(
                and_(
                    col(cls.user_id) == user_id,
                    col(cls.bot_id) == bot_id,
                    col(cls.task_name) == task_name,
                    col(cls.status) == RHComfyuiTaskStatus.FAILED.value,
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
            trace_id=trace_id,
            created_at=created_at,
            entry_point=entry_point,
        )
        session.add(record)
        await session.flush()  # 获取 id
        return int(record.id or 0)

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
        stmt = (
            select(cls)
            .where(and_(col(cls.provider) == provider, col(cls.audio_hash) == audio_hash))
            .limit(1)
        )
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
        stmt = (
            select(cls)
            .where(and_(col(cls.provider) == provider, col(cls.audio_hash) == audio_hash))
            .limit(1)
        )
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
    ]
)
