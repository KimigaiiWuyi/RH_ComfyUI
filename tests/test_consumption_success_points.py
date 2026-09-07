"""消费合计只计成功单,失败预扣不进 total_points。"""

from sqlalchemy.dialects import sqlite

from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord
from RH_ComfyUI.utils.database.consumption import (
    _consumed_points,
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


def test_sum_success_points_sql_uses_ok_case() -> None:
    expr = RHComfyuiTaskRecord.sum_success_points()
    sql = str(expr.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    assert "CASE" in sql.upper()
    assert "ok" in sql
    assert "point_cost" in sql
