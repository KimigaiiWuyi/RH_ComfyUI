"""消费合计只计成功单,失败预扣不进 total_points。"""

from datetime import datetime, timezone, timedelta

from sqlalchemy.dialects import sqlite

from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord
from RH_ComfyUI.utils.database.consumption import (
    _bound_utc,
    _query_window,
    _consumed_points,
    _has_list_filters,
    _aggregate_by_task_type,
)


def test_consumed_points_only_ok() -> None:
    assert _consumed_points("ok", 10) == 10
    assert _consumed_points("failed", 10) == 0
    assert _consumed_points("cancelled", 8) == 0
    assert _consumed_points("running", 5) == 0
    assert _consumed_points("ok", None) == 0


def test_aggregate_by_task_type_ignores_failed_points() -> None:
    records = [
        RHComfyuiTaskRecord(user_id="1", task_type="image", task_name="a", status="ok", point_cost=12),
        RHComfyuiTaskRecord(user_id="1", task_type="image", task_name="a", status="failed", point_cost=12),
        RHComfyuiTaskRecord(user_id="1", task_type="video", task_name="b", status="cancelled", point_cost=40),
    ]
    rows = _aggregate_by_task_type(records)
    by_type = {r["task_type"]: r for r in rows}
    assert by_type["image"]["count"] == 2
    assert by_type["image"]["points"] == 12
    assert by_type["video"]["count"] == 1
    assert by_type["video"]["points"] == 0
    assert rows[0]["task_type"] == "image"


def test_has_list_filters_detects_pipeline() -> None:
    assert _has_list_filters() is False
    assert _has_list_filters(status="ok") is True
    assert _has_list_filters(task_name="seedance2") is True
    assert _has_list_filters(task_name="  ") is False
    assert _has_list_filters(is_refunded=False) is True


def test_bound_utc_converts_beijing_month_start() -> None:
    beijing = timezone(timedelta(hours=8))
    start = datetime(2026, 9, 1, 0, 0, tzinfo=beijing)
    end = datetime(2026, 9, 8, 23, 59, 59, 999000, tzinfo=beijing)
    got_start, got_end = _query_window(None, start, end)
    assert got_start == datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
    assert got_end == datetime(2026, 9, 8, 15, 59, 59, 999000, tzinfo=timezone.utc)
    naive = datetime(2026, 9, 1, 0, 0)
    assert _bound_utc(naive) == datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def test_sum_success_points_sql_uses_ok_case() -> None:
    expr = RHComfyuiTaskRecord.sum_success_points()
    sql = str(expr.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "CASE" in sql.upper()
    assert "ok" in sql
    assert "point_cost" in sql
