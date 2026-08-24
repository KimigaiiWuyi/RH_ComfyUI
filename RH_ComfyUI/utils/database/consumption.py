"""消费记录结构化查询 — bot 命令(文本)和 外部 HTTP(JSON)共用入口。

设计要点:
  - 不重复执行 SQL。Bot 命令原来在 `commands.format_*_consumption` 里
    现取再聚合;外部插件 走 HTTP 也要同样口径,否则两边数据对不上。
    所以**把"取数 + 聚合"集中到这里**,两条调用方各取所需。
  - 只返回 JSON 可序列化的 dict。datetime 由 Pydantic/fastapi jsonable_encoder
    处理;为了避免模型层耦合,RHComfyuiTaskRecord 字段直接展开为 dict。
  - 不做文本渲染;`commands._render_*_text` 负责把 payload 格式化为 bot 消息。

时区约定:
  - DB 存 UTC(`RHComfyuiTaskRecord.created_at` 用 `datetime.now(timezone.utc)`)。
  - **所有面向用户的展示**(HTTP API、bot 消息)统一转北京时区
    (UTC+8,固定偏移),`to_beijing()` 工具函数集中处理。
  - 内部排序 / 区间过滤**仍然按 UTC**(因为 DB 列就是 UTC),不做转换。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional, TypedDict, overload
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .models import RHComfyuiTaskRecord


class TaskTypeBreakdown(TypedDict):
    """按任务类型聚合的一行(_aggregate_by_task_type 的元素)。"""

    task_type: str
    count: int
    points: int


# 北京时区(UTC+8,固定偏移;不用 zoneinfo 是因为 Windows 上 zoneinfo 不可用)
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


@overload
def to_beijing(dt: datetime) -> datetime: ...
@overload
def to_beijing(dt: None) -> None: ...
def to_beijing(dt: Optional[datetime]) -> Optional[datetime]:
    """把任意带 tzinfo 的 datetime 转北京时区(UTC+8)。

    行为:
      * None → None(避免污染 dict 序列化)
      * naive datetime(无 tzinfo)→ 视为 UTC 后转 +08(兼容 DB 直读场景)
      * aware datetime(任何时区)→ astimezone(BEIJING_TZ)
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ)


# ─────────────────────────── 单记录 → dict ───────────────────────────


def _record_to_dict(r: RHComfyuiTaskRecord) -> dict[str, Any]:
    """把单条 RHComfyuiTaskRecord 序列化为 dict(JSON 可序列化)。"""
    return {
        "id": r.id,
        "user_id": r.user_id,
        "bot_id": r.bot_id,
        "group_id": r.group_id,
        "task_type": r.task_type,
        "task_name": r.task_name,
        "backend": r.backend,
        "backend_model": r.backend_model,
        "backend_provider": r.backend_provider,
        "duration_seconds": r.duration_seconds,
        "width": r.width,
        "height": r.height,
        "ratio": r.ratio,
        "resolution": r.resolution,
        "seed": r.seed,
        "voice_id": r.voice_id,
        "prompt": r.prompt or "",
        "status": r.status,
        "is_success": r.is_success,
        "elapsed_ms": r.elapsed_ms,
        "point_cost": int(r.point_cost or 0),
        "refunded": r.refunded,
        "error_message": r.error_message,
        "trace_id": r.trace_id,
        "created_at": to_beijing(r.created_at),
    }


def _aggregate_by_task_type(records: list[RHComfyuiTaskRecord]) -> list[TaskTypeBreakdown]:
    """基于 records 列表聚合任务类型分布(按总积分降序)。

    返回 `[{task_type, count, points}, ...]`,空 records 时返回 `[]`。
    """
    counts: dict[str, int] = {}
    points: dict[str, int] = {}
    for r in records:
        counts[r.task_type] = counts.get(r.task_type, 0) + 1
        points[r.task_type] = points.get(r.task_type, 0) + int(r.point_cost or 0)
    # 按积分降序;同分时按出现频次降序兜底
    return [
        {
            "task_type": k,
            "count": counts[k],
            "points": points[k],
        }
        for k in sorted(counts.keys(), key=lambda x: (-points[x], -counts[x]))
    ]


def _now_window(days: Optional[int]) -> tuple[Optional[datetime], datetime]:
    """根据 days 计算时间窗;None 表示不限制。返回 (start, end) 二元组。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days) if days else None
    return start, end


def _scope_label(days: Optional[int]) -> str:
    return f"最近 {days} 天" if days else "全部时间"


MAX_DAYS = 365
DEFAULT_DAILY_DAYS = 14


def _beijing_day_window(days: int) -> tuple[datetime, datetime]:
    """最近 N 个北京日历日(含今天),返回 UTC aware (start 00:00, end 23:59:59.999999 +08)。"""
    n = min(max(int(days), 1), MAX_DAYS)
    today = to_beijing(datetime.now(timezone.utc)).date()
    start_d = today - timedelta(days=n - 1)
    start = datetime(start_d.year, start_d.month, start_d.day, tzinfo=BEIJING_TZ)
    end = datetime(today.year, today.month, today.day, 23, 59, 59, 999999, tzinfo=BEIJING_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

_EMPTY_SUMMARY: dict[str, Any] = {
    "total": 0,
    "success": 0,
    "failed": 0,
    "success_rate": 0.0,
    "avg_elapsed_ms": 0,
    "total_points": 0,
    "active_users": 0,
    "avg_points": 0.0,
    "by_task_type": {},
}


def fill_daily_gaps(
    rows: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """把稀疏日聚合补成连续北京日历日(缺日补 0),最多 365 天。"""
    by_day = {str(r.get("date") or ""): r for r in rows if r.get("date")}
    start_d = to_beijing(start).date()
    end_d = to_beijing(end).date()
    if end_d < start_d:
        start_d, end_d = end_d, start_d
    out: list[dict[str, Any]] = []
    d = start_d
    while d <= end_d and len(out) < MAX_DAYS:
        key = d.isoformat()
        hit = by_day.get(key)
        out.append(
            {
                "date": key,
                "requests": int(hit.get("requests") or 0) if hit else 0,
                "failed": int(hit.get("failed") or 0) if hit else 0,
                "points": int(hit.get("points") or 0) if hit else 0,
                "users": int(hit.get("users") or 0) if hit else 0,
            }
        )
        d += timedelta(days=1)
    return out


# ─────────────────────────── 用户视图 payload ───────────────────────────


async def build_user_consumption_payload(
    user_id: str,
    bot_id: str,
    *,
    limit: int = 10,
    days: Optional[int] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    task_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    backend: Optional[str] = None,
    backend_model: Optional[str] = None,
    is_refunded: Optional[bool] = None,
    min_points: Optional[int] = None,
    max_points: Optional[int] = None,
    prompt_search: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    """生成"某用户消费记录"的结构化 payload。

    Bot 命令的文本格式化和 外部插件的 HTTP 响应都基于此 payload,
    保证两路数据口径一致。

    Args:
        user_id: 目标用户 ID(字符串)。外部插件 直接传 `str(ctx.id)`。
        bot_id: Bot 平台 ID(如 "qq")。
        limit: 最多返回条数。
        days: 仅统计最近 N 天,None 表示不限制(与 date_from/date_to 互斥)。
        status: 任务状态过滤(running / ok / failed / cancelled)。
        task_type: 任务类型过滤(image / video / music / speech)。
        task_name: Pipeline 节点名过滤(精确匹配,对应表格「Pipeline」列)。
        trace_id: 追踪 ID 过滤(精确匹配)。
        backend: 后端过滤(comfyui / rh_app / seedance / ...)。
        backend_model: 厂商模型 ID 过滤(精确匹配,对应表格「模型」列)。
        is_refunded: 是否已退积分(None=不过滤;True/False 精确匹配)。
        min_points: 最低积分消耗(含)。
        max_points: 最高积分消耗(含)。
        prompt_search: 提示词模糊搜索(数据库 LIKE 匹配)。
        date_from: 起始时间(与 days 互斥;均给值时 date_from 优先)。
        date_to: 截止时间(与 days 互斥)。

    Returns:
        dict(JSON 可序列化),字段见函数体。
    """
    # days 与 date_from/date_to 互斥(同给值优先 date_from)
    if date_from is not None or date_to is not None:
        start, end = date_from, date_to
    else:
        start, end = _now_window(days)
    records = await RHComfyuiTaskRecord.list_by_user(
        user_id=user_id,
        bot_id=bot_id,
        status=status,
        task_type=task_type,
        task_name=task_name,
        trace_id=trace_id,
        backend=backend,
        backend_model=backend_model,
        is_refunded=is_refunded,
        min_points=min_points,
        max_points=max_points,
        prompt_search=prompt_search,
        start_time=start,
        end_time=end,
        limit=limit,
    )
    # 防御: 基类的 @with_session 在重试耗尽时会返回 None(虽然 list(rows)
    # 本身不会,但表未建/数据库失败时整链路会 swallow),统一兜成空列表。
    records = records or []
    record_dicts = [_record_to_dict(r) for r in records]
    success_count = sum(1 for r in records if r.is_success)
    running_count = sum(1 for r in records if r.status == "running")
    failed_count = sum(1 for r in records if r.status in ("failed", "cancelled"))

    return {
        "view": "user",
        "scope": _scope_label(days),
        "user_id": user_id,
        "bot_id": bot_id,
        "filters": {
            "days": days,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            "limit": limit,
            "status": status,
            "task_type": task_type,
            "task_name": task_name,
            "trace_id": trace_id,
            "backend": backend,
            "backend_model": backend_model,
            "is_refunded": is_refunded,
            "min_points": min_points,
            "max_points": max_points,
            "prompt_search": prompt_search,
        },
        "total_count": len(records),
        "total_points": sum(int(r.point_cost or 0) for r in records),
        "success_count": success_count,
        "running_count": running_count,
        # 失败含 cancelled;不含 running(旧口径 total-success 会把进行中算失败)
        "failed_count": failed_count,
        "by_task_type": _aggregate_by_task_type(records),
        "records": record_dicts,
    }


# ─────────────────────────── 管理员视图 payload ───────────────────────────


async def build_admin_consumption_payload(
    bot_id: str,
    *,
    limit: int = 20,
    days: Optional[int] = None,
    top_users: int = 10,
    target_user_id: Optional[str] = None,
    group_id: Optional[str] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    task_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    backend: Optional[str] = None,
    backend_model: Optional[str] = None,
    is_refunded: Optional[bool] = None,
    min_points: Optional[int] = None,
    max_points: Optional[int] = None,
    prompt_search: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    user_id: Optional[str] = None,
    user_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """生成"管理员消费记录"的结构化 payload。

    两种视图:
      - 指定 `target_user_id`: 走单用户聚焦,内部直接委托
        `build_user_consumption_payload`,额外包一层 `view="user"`。
      - 不指定 `target_user_id`: 走全员视图,返回
        `summary / records / user_summaries` 三段。

    `user_id` / `user_ids` 只作用于全员视图(姓名搜索可能命中多人)。
    `user_ids=[]` 表示搜索无命中,直接返回空汇总。
    """
    base_filters = {
        "days": days,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "limit": limit,
        "status": status,
        "task_type": task_type,
        "task_name": task_name,
        "trace_id": trace_id,
        "backend": backend,
        "backend_model": backend_model,
        "is_refunded": is_refunded,
        "min_points": min_points,
        "max_points": max_points,
        "prompt_search": prompt_search,
        "group_id": group_id,
        "top_users": top_users,
        "user_id": user_id,
        "user_ids": user_ids,
    }

    if target_user_id:
        user_payload = await build_user_consumption_payload(
            user_id=target_user_id,
            bot_id=bot_id,
            limit=limit,
            days=days,
            status=status,
            task_type=task_type,
            task_name=task_name,
            trace_id=trace_id,
            backend=backend,
            backend_model=backend_model,
            is_refunded=is_refunded,
            min_points=min_points,
            max_points=max_points,
            prompt_search=prompt_search,
            date_from=date_from,
            date_to=date_to,
        )
        return {
            "view": "user",
            "scope": user_payload["scope"],
            "bot_id": bot_id,
            "target_user_id": target_user_id,
            "filters": base_filters,
            "data": user_payload,
        }

    # days 与 date_from/date_to 互斥(同给值优先 date_from)
    if date_from is not None or date_to is not None:
        start, end = date_from, date_to
    else:
        start, end = _now_window(days)

    if user_ids is not None and len(user_ids) == 0:
        return {
            "view": "global",
            "scope": _scope_label(days),
            "bot_id": bot_id,
            "target_user_id": None,
            "filters": base_filters,
            "summary": dict(_EMPTY_SUMMARY),
            "user_summaries": [],
            "records": [],
            "total_count": 0,
        }

    from .stats_cache import get_summary_cached, get_user_summaries_cached

    has_user_filter = bool(user_id) or bool(user_ids)
    if has_user_filter:
        # 用户筛选是管理员钻取,不进全员 stats 缓存,避免污染 key。
        summary = await RHComfyuiTaskRecord.get_summary(
            start_time=start,
            end_time=end,
            bot_id=bot_id,
            user_id=user_id,
            user_ids=user_ids,
        )
        user_summaries = await RHComfyuiTaskRecord.get_user_summaries(
            start_time=start,
            end_time=end,
            top_n=top_users,
            bot_id=bot_id,
            user_id=user_id,
            user_ids=user_ids,
        )
    else:
        # 1) 全局汇总 — 合并 SQL + 表缓存(L1/L2)
        summary = await get_summary_cached(
            bot_id=bot_id,
            start_time=start,
            end_time=end,
            days=days if date_from is None and date_to is None else None,
        )

        # 2) 按用户聚合 TOP — 条件聚合 + 表缓存
        user_summaries = await get_user_summaries_cached(
            bot_id=bot_id,
            start_time=start,
            end_time=end,
            top_n=top_users,
            days=days if date_from is None and date_to is None else None,
        )
    user_summaries = user_summaries or []

    # 3) 最近明细(轻量 LIMIT;大 JSON defer)。admin HTTP 页另有 /records 分页,
    #    但 bot 文本仍依赖本段 records,故保留。
    records = await RHComfyuiTaskRecord.list_all(
        bot_id=bot_id,
        user_id=user_id,
        user_ids=user_ids,
        group_id=group_id,
        status=status,
        task_type=task_type,
        task_name=task_name,
        trace_id=trace_id,
        backend=backend,
        backend_model=backend_model,
        is_refunded=is_refunded,
        min_points=min_points,
        max_points=max_points,
        prompt_search=prompt_search,
        start_time=start,
        end_time=end,
        limit=limit,
    )
    records = records or []  # 防御:@with_session 重试耗尽返回 None

    return {
        "view": "global",
        "scope": _scope_label(days),
        "bot_id": bot_id,
        "target_user_id": None,
        "filters": base_filters,
        "summary": summary,
        "user_summaries": user_summaries,
        "records": [_record_to_dict(r) for r in records],
        "total_count": len(records),
    }


# ─────────────────────────── 跨用户条件 records payload(管理员) ───────────────────────────


async def build_admin_records_payload(
    bot_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
    days: Optional[int] = None,
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    task_name: Optional[str] = None,
    trace_id: Optional[str] = None,
    backend: Optional[str] = None,
    backend_model: Optional[str] = None,
    is_refunded: Optional[bool] = None,
    min_points: Optional[int] = None,
    max_points: Optional[int] = None,
    prompt_search: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    user_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """管理员专用的"跨用户条件筛选 records"接口。

    与 `build_admin_consumption_payload(view='global')` 的区别:
      - 支持 `offset`(分页)与 `user_id` / `user_ids` 过滤;
      - 不计算 summary / user_summaries(纯列表接口,调用方可重复请求)。

    Args:
        bot_id: Bot 平台。
        limit: 单页条数。
        offset: 分页偏移。
        days: 最近 N 天(与 date_from/date_to 互斥)。
        user_id: 用户 ID 过滤(可选)。
        user_ids: 多用户 IN 过滤(姓名搜索);空列表=无命中。
        group_id: 群号过滤(可选,管理员按群分析)。
        其余过滤项语义同 `list_all`。
    """
    filters = {
        "user_id": user_id,
        "user_ids": user_ids,
        "group_id": group_id,
        "days": days,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "offset": offset,
        "limit": limit,
        "status": status,
        "task_type": task_type,
        "task_name": task_name,
        "trace_id": trace_id,
        "backend": backend,
        "backend_model": backend_model,
        "is_refunded": is_refunded,
        "min_points": min_points,
        "max_points": max_points,
        "prompt_search": prompt_search,
    }
    if user_ids is not None and len(user_ids) == 0:
        return {
            "view": "records",
            "scope": _scope_label(days),
            "bot_id": bot_id,
            "filters": filters,
            "records": [],
            "total_count": 0,
            "has_more": False,
        }
    # days 与 date_from/date_to 互斥
    if date_from is not None or date_to is not None:
        start, end = date_from, date_to
    else:
        start, end = _now_window(days)
    records = await RHComfyuiTaskRecord.list_all(
        bot_id=bot_id,
        user_id=user_id,
        user_ids=user_ids,
        group_id=group_id,
        status=status,
        task_type=task_type,
        task_name=task_name,
        trace_id=trace_id,
        backend=backend,
        backend_model=backend_model,
        is_refunded=is_refunded,
        min_points=min_points,
        max_points=max_points,
        prompt_search=prompt_search,
        start_time=start,
        end_time=end,
        offset=offset,
        limit=limit,
    )
    records = records or []  # 防御:@with_session 重试耗尽返回 None
    return {
        "view": "records",
        "scope": _scope_label(days),
        "bot_id": bot_id,
        "filters": filters,
        "records": [_record_to_dict(r) for r in records],
        "total_count": len(records),
        "has_more": len(records) == limit,
    }


# ─────────────────────────── 每日趋势 payload(管理员) ───────────────────────────


async def build_admin_daily_payload(
    bot_id: str,
    *,
    days: Optional[int] = None,
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
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict[str, Any]:
    """管理员每日趋势:请求数 / 失败数 / 积分 / 去重用户数,按北京日历日,缺日补 0。

    未给 days / date_from / date_to 时默认最近 DEFAULT_DAILY_DAYS 天,
    避免「全部时间」把整表扫进图表。上限 MAX_DAYS。
    `user_ids=[]` 表示姓名搜索无命中。
    """
    defaulted = False
    if date_from is not None or date_to is not None:
        if date_from is None:
            date_from = (date_to or datetime.now(timezone.utc)) - timedelta(days=DEFAULT_DAILY_DAYS)
        if date_to is None:
            date_to = datetime.now(timezone.utc)
        start, end = date_from, date_to
        span = (to_beijing(end).date() - to_beijing(start).date()).days + 1
        if span > MAX_DAYS:
            start = end - timedelta(days=MAX_DAYS - 1)
    elif days is not None:
        start, end = _beijing_day_window(days)
    else:
        defaulted = True
        start, end = _beijing_day_window(DEFAULT_DAILY_DAYS)

    filters = {
        "user_id": user_id,
        "user_ids": user_ids,
        "group_id": group_id,
        "days": days if not defaulted else (days or DEFAULT_DAILY_DAYS),
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "status": status,
        "task_type": task_type,
        "task_name": task_name,
        "backend": backend,
        "backend_model": backend_model,
        "is_refunded": is_refunded,
        "min_points": min_points,
        "max_points": max_points,
        "prompt_search": prompt_search,
        "defaulted_days": defaulted,
    }
    empty_days = fill_daily_gaps([], start, end) if start and end else []
    if user_ids is not None and len(user_ids) == 0:
        return {
            "view": "daily",
            "scope": _scope_label(filters["days"] if isinstance(filters["days"], int) else None),
            "bot_id": bot_id,
            "filters": filters,
            "days": empty_days,
        }

    rows = await RHComfyuiTaskRecord.get_daily_series(
        bot_id=bot_id,
        user_id=user_id,
        user_ids=user_ids,
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
        start_time=start,
        end_time=end,
    )
    rows = rows or []
    return {
        "view": "daily",
        "scope": _scope_label(filters["days"] if isinstance(filters["days"], int) else None),
        "bot_id": bot_id,
        "filters": filters,
        "days": fill_daily_gaps(rows, start, end),
    }


# 筛选项几乎不随每次生成变,60s 进程缓存避免管理页连点/多 Tab 反复 DISTINCT。
_FILTER_OPTIONS_TTL_SEC = 60.0
_FILTER_OPTIONS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FILTER_OPTIONS_CACHE_MAX = 16


async def build_filter_options_payload(bot_id: str) -> dict[str, Any]:
    """管理页筛选下拉:当前 bot 池里实际出现过的 Pipeline / 模型 / 后端。"""
    key = bot_id or ""
    now = time.monotonic()
    hit = _FILTER_OPTIONS_CACHE.get(key)
    if hit is not None and hit[0] > now:
        return hit[1]
    data = await RHComfyuiTaskRecord.list_filter_options(bot_id=bot_id)
    data = data or {}
    payload = {
        "view": "filter_options",
        "bot_id": bot_id,
        "pipelines": list(data.get("pipelines") or []),
        "models": list(data.get("models") or []),
        "backends": list(data.get("backends") or []),
    }
    if len(_FILTER_OPTIONS_CACHE) >= _FILTER_OPTIONS_CACHE_MAX:
        expired = [k for k, (exp, _) in _FILTER_OPTIONS_CACHE.items() if exp <= now]
        for k in expired:
            _FILTER_OPTIONS_CACHE.pop(k, None)
        if len(_FILTER_OPTIONS_CACHE) >= _FILTER_OPTIONS_CACHE_MAX:
            oldest = min(_FILTER_OPTIONS_CACHE, key=lambda k: _FILTER_OPTIONS_CACHE[k][0])
            _FILTER_OPTIONS_CACHE.pop(oldest, None)
    _FILTER_OPTIONS_CACHE[key] = (now + _FILTER_OPTIONS_TTL_SEC, payload)
    return payload


# ─────────────────────────── 单条记录详情 payload ───────────────────────────


def _parse_saved_files(raw: str) -> list[str]:
    """解析 saved_files_json 列(相对 OUTPUT_PATH 的路径 JSON 数组)。

    旧记录为空串;解析失败/形状不对一律返回 [](该列由 statistics 写入,
    正常不会坏,防御历史手工数据)。
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [p for p in parsed if isinstance(p, str) and p]


async def build_record_detail_payload(record_id: int) -> Optional[dict[str, Any]]:
    """单条消费记录详情(含 raw_response_json 等重字段)。

    列表接口刻意不返回 raw_response_json(最大 64KB,随分页批量返回会拖垮
    响应体积);前端点击行展开时懒加载走这里。记录不存在返回 None。
    """
    record = await RHComfyuiTaskRecord.get_by_record_id(record_id)
    if record is None:
        return None
    detail = _record_to_dict(record)
    detail.update(
        {
            "entry_point": record.entry_point,
            "backend_key_prefix": record.backend_key_prefix,
            "extra_params_json": record.extra_params_json,
            "request_body_json": record.request_body_json,
            "raw_response_json": record.raw_response_json,
            "saved_files": _parse_saved_files(record.saved_files_json),
        }
    )
    return detail


async def resolve_record_saved_file(record_id: int, file_index: int) -> Optional[Path]:
    """把 (record_id, saved_files 下标) 映射为 OUTPUT_PATH 内的实际文件路径。

    供 HTTP 层按下标流式返回产物文件 —— 调用方只拿到
    下标而非路径,所有路径解析收在这里并强制限制在 OUTPUT_PATH 目录内
    (相对路径含 ../ 或符号链接逃逸都会被拒),文件不存在/越界返回 None。
    """
    record = await RHComfyuiTaskRecord.get_by_record_id(record_id)
    if record is None:
        return None
    files = _parse_saved_files(record.saved_files_json)
    if file_index < 0 or file_index >= len(files):
        return None
    from ..resource.RESOURCE_PATH import OUTPUT_PATH

    base = Path(OUTPUT_PATH).resolve()
    try:
        target = (base / files[file_index]).resolve()
        target.relative_to(base)  # 目录穿越防护
    except (ValueError, OSError):
        return None
    if not target.is_file():
        return None
    return target


__all__ = [
    "build_user_consumption_payload",
    "build_admin_consumption_payload",
    "build_admin_records_payload",
    "build_admin_daily_payload",
    "build_filter_options_payload",
    "build_record_detail_payload",
    "resolve_record_saved_file",
    "fill_daily_gaps",
    "to_beijing",
    "DEFAULT_DAILY_DAYS",
]
