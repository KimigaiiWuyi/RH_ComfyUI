"""积分管理命令的执行函数.

该模块包含所有积分管理相关的核心逻辑函数,
包括增加积分、减少积分、查询积分等功能.
所有核心函数都同时作为 AI Tools 注册.
"""

from typing import Tuple, Optional, Protocol, Annotated
from datetime import datetime, timezone, timedelta

from msgspec import Meta

from gsuid_core.models import Event
from gsuid_core.ai_core.register import ai_tools

from ..utils.database.models import RHBind, RHComfyuiTaskRecord
from ..utils.database.consumption import (
    to_beijing,
    build_user_consumption_payload,
    build_admin_consumption_payload,
)

# ============================================================
# 参数解析函数
# ============================================================


async def parse_add_points_args(ev: Event) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """解析增加/减少积分命令的参数.

    Args:
        ev: Event 实例

    Returns:
        返回 (target_user_id, point_num, error_message)
        如果解析成功，error_message 为 None
        如果解析失败，target_user_id 和 point_num 为 None
    """
    args: list[str] = ev.text.strip().split()

    if not ev.text.strip():
        return None, None, "📋 格式: 增加积分 <@用户> <积分数量>"

    target_user_id: Optional[str] = None
    point_num: Optional[int] = None

    if ev.at:
        target_user_id = ev.at
        try:
            point_num = int(args[0])
        except (ValueError, IndexError):
            return None, None, "⚠️ 积分数量必须是数字！"
    else:
        try:
            point_num = int(args[1])
            target_user_id = args[0]
        except (ValueError, IndexError):
            return None, None, "❌ 格式错误！使用: 增加积分 <@用户> <积分数量>"

    if point_num <= 0:
        return None, None, "⚠️ 积分数量必须大于0！"

    return target_user_id, point_num, None


async def parse_query_points_args(ev: Event) -> tuple[str, Optional[str]]:
    """解析查询积分命令的参数.

    Args:
        ev: Event 实例

    Returns:
        返回 (target_user_id, error_message)
        如果解析成功，error_message 为 None
    """
    target_user_id: Optional[str] = None

    if ev.at:
        target_user_id = ev.at
    else:
        args: list[str] = ev.text.strip().split()
        if args:
            target_user_id = args[0]
        else:
            target_user_id = ev.user_id

    if ev.user_pm != 0 and ev.user_id != target_user_id:
        return target_user_id or "", "🚫 您不是管理员，无法查询其他用户积分！"

    return target_user_id or ev.user_id, None


# ============================================================
# AI Tools (同时也是核心实现)
# ============================================================


def check_pm(ev: Event) -> Tuple[bool, str]:
    """检查用户是否为管理员.

    Args:
        ev: Event 实例,包含事件相关信息

    Returns:
        如果用户是管理员,返回 True;否则返回 False
    """
    if ev.user_pm == 0:
        return True, "✅ 您是管理员，为你进行操作！"
    return False, "🚫 您不是管理员，无法执行此操作！"


@ai_tools(check_func=check_pm)
async def add_user_points(
    target_user_id: Annotated[str, Meta(description="目标用户的唯一标识 ID")],
    point_num: Annotated[int, Meta(description="要增加的积分数量,必须大于 0")],
    ev: Event,
) -> str:
    """增加指定用户的积分.

    该工具用于为特定用户增加积分点数,常用于奖励用户、补偿积分或进行活动赠送.

    Args:
        target_user_id: 目标用户的唯一标识 ID
        point_num: 要增加的积分数量,必须大于 0
        ev: Event 实例,包含事件相关信息

    Returns:
        操作结果描述字符串,包含成功/失败信息和当前积分
    """
    result: int = await RHBind.add_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
        add_point_num=point_num,
    )

    if result == 0:
        current_point: int = await RHBind.get_point(
            user_id=target_user_id,
            bot_id=ev.bot_id,
        )
        return f"✅ 成功为用户 [{target_user_id}] 增加 {point_num} 积分！\n📋 当前积分: {current_point}"
    else:
        return "❌ 增加积分失败！"


@ai_tools(check_func=check_pm)
async def deduct_user_points(
    target_user_id: Annotated[str, Meta(description="目标用户的唯一标识 ID")],
    point_num: Annotated[int, Meta(description="要扣除的积分数量,必须大于 0")],
    ev: Event,
) -> str:
    """扣除指定用户的积分.

    该工具用于从特定用户扣除积分点数,常用于消费积分、惩罚或进行积分调整.
    如果用户积分不足,将扣除全部剩余积分.

    Args:
        target_user_id: 目标用户的唯一标识 ID
        point_num: 要扣除的积分数量,必须大于 0
        ev: Event 实例,包含事件相关信息

    Returns:
        操作结果描述字符串,包含成功/失败信息和当前积分
    """
    current_point: int = await RHBind.get_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
    )

    if current_point < point_num:
        point_num = current_point

    result: bool = await RHBind.deduct_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
        deduct_point_num=point_num,
    )

    if result:
        new_point: int = await RHBind.get_point(
            user_id=target_user_id,
            bot_id=ev.bot_id,
        )
        return f"✅ 成功为用户 [{target_user_id}] 扣除 {point_num} 积分！\n📋 当前积分: {new_point}"
    else:
        return "❌ 扣除积分失败！"


@ai_tools
async def query_user_points(
    target_user_id: Annotated[str, Meta(description="目标用户的唯一标识 ID")],
    ev: Event,
) -> str:
    """查询指定用户的当前积分.

    该工具用于获取特定用户的当前积分余额,可用于查询自己的积分或管理员查询其他用户积分.

    Args:
        target_user_id: 目标用户的唯一标识 ID
        ev: Event 实例,包含事件相关信息

    Returns:
        包含用户当前积分的描述字符串
    """
    current_point: int = await RHBind.get_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
    )

    return f"👤 用户 [{target_user_id}] 的当前积分: {current_point}"


# ═══════════════════════════════════════════════════════════════════════
#  任务执行统计 AI 工具
# ═══════════════════════════════════════════════════════════════════════


@ai_tools
async def query_task_records(
    target_user_id: Annotated[str, Meta(description="目标用户 ID")],
    ev: Event,
    status: Annotated[Optional[str], Meta(description="ok/failed,留空=全部")] = None,
    task_type: Annotated[Optional[str], Meta(description="image/video/music/speech,留空=全部")] = None,
    days: Annotated[int, Meta(description="查询最近 N 天,默认 7")] = 7,
    limit: Annotated[int, Meta(description="最多返回条数,默认 20")] = 20,
) -> str:
    """查询某用户最近的任务执行记录(管理员 / 本人可查本人)"""
    if ev.user_pm != 0 and target_user_id != ev.user_id:
        return "🚫 无权查询其他用户的任务记录"

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    records = await RHComfyuiTaskRecord.list_by_user(
        user_id=target_user_id,
        bot_id=ev.bot_id,
        status=status,
        task_type=task_type,
        start_time=start,
        end_time=end,
        limit=limit,
    )

    if not records:
        return f"📭 用户 {target_user_id} 在最近 {days} 天内无任务记录"

    lines = [f"📊 用户 {target_user_id} 最近 {days} 天任务记录(共 {len(records)} 条):\n"]
    for r in records:
        # DB 存 UTC,bot 给用户看时统一转北京(UTC+8);_format_record_line 同理
        ts = to_beijing(r.created_at).strftime("%Y-%m-%d %H:%M:%S")
        status_icon = "✅" if r.is_success else "❌"
        elapsed_text = f"{r.elapsed_ms}ms" if r.elapsed_ms is not None else "?ms"
        lines.append(f"{status_icon} [{ts}] {r.task_name}({r.task_type}) {elapsed_text} 积分={r.point_cost}")
        if r.error_message:
            lines.append(f"   错误: {r.error_message[:200]}")
    return "\n".join(lines)


@ai_tools
async def task_stats_summary(
    ev: Event,
    days: Annotated[int, Meta(description="统计最近 N 天,默认 7")] = 7,
) -> str:
    """查询最近 N 天的全局任务执行摘要(管理员限定)"""
    if ev.user_pm != 0:
        return "🚫 仅管理员可查全局统计"

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    summary = await RHComfyuiTaskRecord.get_summary(start_time=start, end_time=end)

    lines = [
        f"📈 最近 {days} 天任务统计:",
        f"  总数: {summary['total']}",
        f"  成功: {summary['success']}",
        f"  失败: {summary['failed']}",
        f"  成功率: {summary['success_rate'] * 100:.2f}%",
        f"  平均耗时: {summary['avg_elapsed_ms']}ms",
        "  按类型: " + ", ".join(f"{k}={v}" for k, v in summary["by_task_type"].items()),
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  消费记录查看(用户 / 管理员命令行直接调用)
# ═══════════════════════════════════════════════════════════════════════


_TASK_TYPE_LABEL: dict[str, str] = {
    "image": "图片",
    "video": "视频",
    "music": "音乐",
    "speech": "语音",
}


class _RecordLike(Protocol):
    """_format_record_line 需要的字段(RHComfyuiTaskRecord 与 _DictRecord 都满足)。"""

    @property
    def is_success(self) -> bool: ...
    @property
    def created_at(self) -> Optional[datetime]: ...
    @property
    def task_name(self) -> str: ...
    @property
    def task_type(self) -> str: ...
    @property
    def user_id(self) -> str: ...
    @property
    def elapsed_ms(self) -> Optional[int]: ...
    @property
    def point_cost(self) -> int: ...
    @property
    def error_message(self) -> str: ...


def _format_record_line(record: _RecordLike, *, show_user: bool = False) -> str:
    """把单条任务记录格式化为一行文本"""
    status_icon = "✅" if record.is_success else "❌"
    # DB 存 UTC,bot 给用户看时统一转北京(UTC+8)
    bj = to_beijing(record.created_at)
    ts = bj.strftime("%m-%d %H:%M") if bj is not None else "--"
    type_label = _TASK_TYPE_LABEL.get(record.task_type, record.task_type)
    user_part = f" [{record.user_id}]" if show_user else ""
    elapsed = f"{record.elapsed_ms}ms" if record.elapsed_ms is not None else "?ms"
    line = f"{status_icon} {ts}{user_part} {record.task_name}({type_label}) -{record.point_cost}积分 {elapsed}"
    if not record.is_success and record.error_message:
        # 错误摘要单行截断,避免刷屏
        err = record.error_message.splitlines()[0][:60]
        line += f"\n   ↳ 失败: {err}"
    return line


async def format_user_consumption(
    user_id: str,
    bot_id: str,
    *,
    limit: int = 10,
    days: Optional[int] = None,
) -> str:
    """生成某用户的消费记录文本(供 sv_user 命令调用)

    实现:`build_user_consumption_payload` 取数 + 聚合,本函数只负责渲染。
    保证与 canvas_backend HTTP 接口口径一致。

    Args:
        user_id: 目标用户 ID
        bot_id: Bot 平台
        limit: 最多返回条数
        days: 仅统计最近 N 天(None 表示不限制时间)
    """
    payload = await build_user_consumption_payload(
        user_id=user_id,
        bot_id=bot_id,
        limit=limit,
        days=days,
    )
    return _render_user_text(payload)


def _render_user_text(payload: dict) -> str:
    """把 user-view payload 渲染为 bot 消息文本(emoji 风格保持兼容)。"""
    records = payload.get("records", [])
    if not records:
        return f"📭 用户 {payload['user_id']} 在{payload['scope']}内暂无任务记录"

    # 把 record dict 重新套成轻量对象,复用 _format_record_line 的字段访问
    record_objs = [_DictRecord(d) for d in records]

    lines: list[str] = [
        f"📊 用户 {payload['user_id']} 的消费记录"
        f"(共 {payload['total_count']} 条,共消耗 {payload['total_points']} 积分):",
        "",
    ]
    lines.extend(_format_record_line(r) for r in record_objs)

    lines.append("")
    lines.append(f"📈 成功 {payload['success_count']} / 失败 {payload['failed_count']}")

    if payload.get("by_task_type"):
        lines.append("📋 任务类型分布:")
        for entry in payload["by_task_type"]:
            ttype = entry["task_type"]
            label = _TASK_TYPE_LABEL.get(ttype, ttype)
            lines.append(f"  · {label}({ttype}): {entry['count']}次 / {entry['points']}积分")
    return "\n".join(lines)


class _DictRecord:
    """轻量 record 适配器,让 _format_record_line 接受 dict 输入。"""

    __slots__ = ("_d",)

    def __init__(self, d: dict) -> None:
        self._d = d

    @property
    def is_success(self) -> bool:
        return bool(self._d.get("is_success"))

    @property
    def created_at(self):
        # payload 里的 created_at 已经被 _record_to_dict 转过北京时区了;
        # 此处直接返回,_format_record_line 的 strftime 拿到的就是 +08 时间
        return self._d.get("created_at")

    @property
    def task_name(self) -> str:
        return str(self._d.get("task_name", ""))

    @property
    def task_type(self) -> str:
        return str(self._d.get("task_type", ""))

    @property
    def user_id(self) -> str:
        return str(self._d.get("user_id", ""))

    @property
    def elapsed_ms(self):
        return self._d.get("elapsed_ms")

    @property
    def point_cost(self) -> int:
        return int(self._d.get("point_cost", 0) or 0)

    @property
    def error_message(self) -> str:
        return str(self._d.get("error_message", "") or "")


async def format_admin_consumption(
    bot_id: str,
    *,
    limit: int = 20,
    days: Optional[int] = None,
    top_users: int = 10,
) -> str:
    """生成全员消费记录文本(供 sv_admin/pm=0 命令调用)

    实现:`build_admin_consumption_payload` 取数 + 聚合,本函数只负责渲染。

    Args:
        bot_id: Bot 平台
        limit: 最多列出多少条原始记录
        days: 仅统计最近 N 天(None 表示不限制)
        top_users: 顶部用户消费榜显示多少名
    """
    payload = await build_admin_consumption_payload(
        bot_id=bot_id,
        limit=limit,
        days=days,
        top_users=top_users,
    )
    return _render_admin_text(payload)


def _render_admin_text(payload: dict) -> str:
    """把 admin-view payload 渲染为 bot 消息文本。"""
    scope = payload.get("scope", "")
    summary = payload.get("summary") or {}
    records = payload.get("records", [])
    user_summaries = payload.get("user_summaries") or []

    lines: list[str] = [
        f"📊 全员消费记录({scope})",
        "",
        "📈 全局汇总:",
        f"  · 总任务: {summary.get('total', 0)} 条",
        f"  · 成功: {summary.get('success', 0)} / 失败: {summary.get('failed', 0)}",
        f"  · 成功率: {summary.get('success_rate', 0.0) * 100:.2f}%",
        f"  · 平均耗时: {summary.get('avg_elapsed_ms', 0)}ms",
    ]
    by_type = summary.get("by_task_type") or {}
    if by_type:
        lines.append("  · 任务类型分布:")
        # 字典在 JSON 路径上保留为 dict,按总积分降序
        for ttype, cnt in sorted(by_type.items(), key=lambda kv: -kv[1]):
            label = _TASK_TYPE_LABEL.get(ttype, ttype)
            lines.append(f"      {label}({ttype}): {cnt}条")

    lines.append("")
    if records:
        record_objs = [_DictRecord(d) for d in records]
        lines.append(f"📝 最近 {len(records)} 条原始记录:")
        lines.extend(_format_record_line(r, show_user=True) for r in record_objs)
    else:
        lines.append(f"📭 {scope}内暂无任务记录")

    lines.append("")
    if user_summaries:
        lines.append(f"🏆 用户消费榜 TOP {len(user_summaries)}:")
        for i, u in enumerate(user_summaries, 1):
            lines.append(
                f"  {i}. {u['user_id']} — {u['total']}条 / "
                f"{u['total_points']}积分 (成功{u['success']}/失败{u['failed']})"
            )
    else:
        lines.append("🏆 用户消费榜: 暂无数据")

    return "\n".join(lines)
