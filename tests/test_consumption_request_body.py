import asyncio

from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord
from RH_ComfyUI.utils.database.consumption import (
    _record_to_dict,
    build_record_detail_payload,
)


def test_request_body_is_only_exposed_in_record_detail(monkeypatch) -> None:
    record = RHComfyuiTaskRecord(
        user_id="u1",
        task_type="image",
        task_name="image_node",
        status="ok",
        request_body_json='{"prompt":"cat"}',
    )

    async def fake_get(cls, record_id: int):
        assert record_id == 7
        return record

    monkeypatch.setattr(RHComfyuiTaskRecord, "get_by_record_id", classmethod(fake_get))

    list_item = _record_to_dict(record)
    detail = asyncio.run(build_record_detail_payload(7))

    assert "request_body_json" not in list_item
    assert detail is not None
    assert detail["request_body_json"] == '{"prompt":"cat"}'
