"""fill_daily_gaps: 稀疏日聚合补成连续北京日历日。"""

from datetime import datetime, timezone, timedelta

from RH_ComfyUI.utils.database.consumption import BEIJING_TZ, fill_daily_gaps


def test_fill_daily_gaps_inserts_zeros():
    start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"date": "2026-08-01", "requests": 3, "points": 10, "users": 2},
        {"date": "2026-08-03", "requests": 1, "points": 4, "users": 1},
    ]
    out = fill_daily_gaps(rows, start, end)
    dates = [r["date"] for r in out]
    # UTC 8/1 00:00 = 北京 8/1 08:00; UTC 8/4 12:00 = 北京 8/4 20:00
    assert dates[0] == "2026-08-01"
    assert dates[-1] == "2026-08-04"
    by_date = {r["date"]: r for r in out}
    assert by_date["2026-08-01"]["requests"] == 3
    assert by_date["2026-08-02"]["requests"] == 0
    assert by_date["2026-08-02"]["failed"] == 0
    assert by_date["2026-08-02"]["points"] == 0
    assert by_date["2026-08-02"]["users"] == 0
    assert by_date["2026-08-03"]["points"] == 4


def test_beijing_day_window_inclusive_count():
    from RH_ComfyUI.utils.database.consumption import _beijing_day_window

    start, end = _beijing_day_window(14)
    filled = fill_daily_gaps([], start, end)
    assert len(filled) == 14
    start30, end30 = _beijing_day_window(30)
    assert len(fill_daily_gaps([], start30, end30)) == 30


def test_fill_daily_gaps_empty_rows_still_emits_range():
    start = datetime(2026, 8, 10, 16, 0, tzinfo=timezone.utc)  # 北京 8/11 00:00
    end = start + timedelta(hours=5)
    out = fill_daily_gaps([], start, end)
    assert [r["date"] for r in out] == ["2026-08-11"]
    assert out[0]["requests"] == 0
    assert start.astimezone(BEIJING_TZ).date().isoformat() == "2026-08-11"
