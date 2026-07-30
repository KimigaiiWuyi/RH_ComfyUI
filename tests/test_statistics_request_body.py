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
    assert stored["params"]["image_base64"].startswith("<base64://")
    assert stored["params"]["image_base64"].endswith("#4>")
    assert stored["params"]["nested"]["text"] == "keep this value"
    assert "AQID" not in captured["request_body_json"]


def test_begin_then_record_updates_same_row(monkeypatch) -> None:
    """创建写 running,结束 UPDATE 同一 id(不二次 INSERT)。"""
    inserts: list[dict] = []
    updates: list[dict] = []

    async def fake_insert(**kwargs):
        inserts.append(kwargs)
        return 7

    async def fake_update(record_id, **kwargs):
        updates.append({"record_id": record_id, **kwargs})
        return True

    monkeypatch.setattr(RHComfyuiTaskRecord, "insert_task_record", fake_insert)
    monkeypatch.setattr(RHComfyuiTaskRecord, "update_task_record", fake_update)

    request = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", user_id="u1")
    node = SimpleNamespace(
        name="n1",
        backend="fake",
        provider="",
        backend_model="",
        backend_models={},
        point_cost=5,
    )

    async def _run() -> None:
        rid = await statistics.begin_task(request=request, node=node, bot_id="canvas", point_cost=5)
        assert rid == 7
        assert inserts[0]["status"] == "running"
        assert inserts[0]["point_cost"] == 5
        out = await statistics.record_task(
            request=request,
            result=None,
            node=node,
            status="ok",
            elapsed_ms=100,
            record_id=rid,
            point_cost=5,
        )
        assert out == 7

    asyncio.run(_run())
    assert len(inserts) == 1
    assert len(updates) == 1
    assert updates[0]["record_id"] == 7
    assert updates[0]["status"] == "ok"
    assert updates[0]["elapsed_ms"] == 100
