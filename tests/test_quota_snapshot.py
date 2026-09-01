"""管理端额度列表:只读 snapshot 不写库、不改行。"""

from RH_ComfyUI.utils.database.models import RHBind
from RH_ComfyUI.core.billing.tier_quota import get_tier_quotas


def _row(
    *,
    h5: int,
    day: int,
    week: int,
    r5: int = 0,
    rd: int = 1,
    rw: int = 1,
    tier: str = "free",
) -> RHBind:
    return RHBind(
        user_id="1",
        bot_id="canvas",
        point=min(h5, day, week),
        point_5h=h5,
        point_day=day,
        point_week=week,
        refreshed_at_5h=r5,
        refreshed_at_day=rd,
        refreshed_at_week=rw,
        vip_tier=tier,
    )


def test_snapshot_missing_wallet_shows_full_caps():
    q = get_tier_quotas("free")
    st = RHBind.snapshot_quota_status(None, vip_tier="free")
    assert st["unlimited"] is False
    assert st["buckets"]["h5"]["balance"] == q.h5
    assert st["buckets"]["day"]["balance"] == q.day
    assert st["buckets"]["week"]["balance"] == q.week
    assert st["available"] == min(q.h5, q.day, q.week)


def test_snapshot_keeps_stored_balances_when_not_due():
    row = _row(h5=100, day=200, week=300, rd=2**31 - 1, rw=2**31 - 1)
    st = RHBind.snapshot_quota_status(row, vip_tier="free")
    assert st["buckets"]["h5"]["balance"] == 100
    assert st["buckets"]["day"]["balance"] == 200
    assert st["buckets"]["week"]["balance"] == 300
    assert row.point_5h == 100
    assert row.point_day == 200


def test_snapshot_virtual_day_refresh_does_not_mutate_row():
    q = get_tier_quotas("free")
    row = _row(h5=100, day=1, week=300, rd=1, rw=2**31 - 1)
    st = RHBind.snapshot_quota_status(row, vip_tier="free")
    assert st["buckets"]["day"]["balance"] == q.day
    assert row.point_day == 1


def test_snapshot_unlimited_flag():
    st = RHBind.snapshot_quota_status(None, vip_tier="unlimited")
    assert st["unlimited"] is True
    assert st["buckets"]["h5"]["unlimited"] is True
