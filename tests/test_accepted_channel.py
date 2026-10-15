"""上游接单后，进行中的消费行立刻写入通道名。"""

from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord


def _row(status: str, provider: str = "") -> RHComfyuiTaskRecord:
    return RHComfyuiTaskRecord(
        user_id="u1",
        task_type="video",
        task_name="seedance2",
        status=status,
        backend_provider=provider,
    )


def test_running_row_takes_the_accepted_channel() -> None:
    row = _row("running")
    RHComfyuiTaskRecord.stamp_accepted_channel(row, "ark")
    assert row.backend_provider == "ark"


def test_later_accept_replaces_the_channel_while_running() -> None:
    row = _row("running", "ark")
    RHComfyuiTaskRecord.stamp_accepted_channel(row, "gateway_slot1")
    assert row.backend_provider == "gateway_slot1"


def test_channel_is_stored_at_the_column_limit() -> None:
    row = _row("running")
    RHComfyuiTaskRecord.stamp_accepted_channel(row, "g" * 40)
    assert row.backend_provider == "g" * 32


def test_blank_channel_does_not_clear_a_running_provider() -> None:
    row = _row("running", "ark")
    RHComfyuiTaskRecord.stamp_accepted_channel(row, "  ")
    assert row.backend_provider == "ark"


def test_finished_row_keeps_the_settled_provider() -> None:
    row = _row("ok", "ark")
    RHComfyuiTaskRecord.stamp_accepted_channel(row, "gateway_slot1")
    assert row.backend_provider == "ark"
