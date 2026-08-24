"""消费记录列表筛选:Pipeline / 模型 / 后端都是精确等值。"""

from sqlalchemy.dialects import sqlite

from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord


def _sql(expr) -> str:
    compiled = expr.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled)


def test_append_list_filters_pipeline_and_model_are_exact():
    conds: list = []
    RHComfyuiTaskRecord._append_list_filters(
        conds,
        task_name="seedance2",
        backend_model="doubao-seedance-2-0-mini-260615",
        backend="seedance",
    )
    by_kind: dict[str, str] = {}
    for expr in conds:
        sql = _sql(expr)
        upper = sql.upper()
        if "BACKEND_MODEL" in upper:
            by_kind["model"] = sql
        elif "TASK_NAME" in upper:
            by_kind["pipeline"] = sql
        elif "BACKEND" in upper:
            by_kind["backend"] = sql
    assert "LIKE" not in by_kind["pipeline"].upper()
    assert "seedance2" in by_kind["pipeline"]
    assert "LIKE" not in by_kind["model"].upper()
    assert "doubao-seedance-2-0-mini-260615" in by_kind["model"]
    assert "LIKE" not in by_kind["backend"].upper()
    assert "seedance" in by_kind["backend"]


def test_append_list_filters_skips_blank_pipeline_and_model():
    conds: list = []
    RHComfyuiTaskRecord._append_list_filters(conds, task_name="  ", backend_model="")
    assert conds == []
