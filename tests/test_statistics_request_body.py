import json
import asyncio
from types import SimpleNamespace

from RH_ComfyUI.utils.database import statistics
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord


def test_record_task_writes_complete_sanitized_request_body(monkeypatch) -> None:
    captured: dict = {}

    async def fake_insert(**kwargs):
        captured.update(kwargs)
        return 42

    monkeypatch.setattr(RHComfyuiTaskRecord, "insert_task_record", fake_insert)

    request = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="keep this prompt",
        params={
            "image_base64": "AQID",
            "nested": {"text": "keep this value"},
        },
    )
    original_body = {
        "task_type": "image",
        "prompt": "keep this prompt",
        "params": {
            "image_base64": "AQID",
            "nested": {"text": "keep this value"},
        },
    }
    node = SimpleNamespace(
        name="image_node",
        backend="fake",
        provider="",
        backend_model="",
        backend_models={},
        point_cost=3,
    )

    record_id = asyncio.run(
        statistics.record_task(
            request=request,
            request_body=original_body,
            result=None,
            node=node,
            status="ok",
            elapsed_ms=12,
        )
    )

    assert record_id == 42
    stored = json.loads(captured["request_body_json"])
    assert stored["prompt"] == "keep this prompt"
    assert stored["params"]["image_base64"] == "<base64 len=4>"
    assert stored["params"]["nested"]["text"] == "keep this value"
    assert "AQID" not in captured["request_body_json"]
