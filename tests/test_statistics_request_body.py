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


def test_wire_audit_mirrors_to_active_generation() -> None:
    """set_wire 同步镜像 ActiveGeneration;clear 后两边都空。"""
    from RH_ComfyUI.core.dispatch.active_tasks import get_active_task_registry
    from RH_ComfyUI.core.telemetry.wire_capture import (
        get_wire_audit,
        set_wire_audit,
        clear_wire_audit,
    )

    async def _run() -> None:
        reg = get_active_task_registry()
        clear_wire_audit()
        ag = await reg.register(model_name="seedance2", trace_id="wire-mirror-1")
        try:
            set_wire_audit(prompt="最终提示", request={"model": "x", "prompt": "最终提示"})
            snap = get_wire_audit()
            assert snap["prompt"] == "最终提示"
            assert ag.wire_prompt == "最终提示"
            assert isinstance(ag.wire_request, dict)
            assert ag.wire_request["model"] == "x"
        finally:
            await reg.unregister(ag)
            clear_wire_audit()
        assert get_wire_audit() == {}

    asyncio.run(_run())


def test_record_task_prefers_wire_audit_prompt_and_body(monkeypatch) -> None:
    """终态落库优先使用 backend 写入的 wire(最终 prompt / HTTP body)。"""
    from RH_ComfyUI.core.telemetry.wire_capture import clear_wire_audit, set_wire_from_http_body

    captured: dict = {}

    async def fake_insert(**kwargs):
        captured.update(kwargs)
        return 99

    monkeypatch.setattr(RHComfyuiTaskRecord, "insert_task_record", fake_insert)

    request = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="看[参考图片1]奔跑",  # 调用方入参
    )
    node = SimpleNamespace(
        name="seedance2",
        backend="seedance",
        provider="ark",
        backend_model="m",
        backend_models={},
        point_cost=10,
    )

    async def _run() -> None:
        clear_wire_audit()
        # 模拟 provider 发往上游前的最终 body(已改写引用)
        set_wire_from_http_body(
            {
                "model": "doubao-seedance-2-0",
                "content": [
                    {"type": "text", "text": "看【图片1】奔跑"},
                    {"type": "image_url", "image_url": {"url": "https://cdn.example/a.png"}},
                ],
            }
        )
        await statistics.record_task(
            request=request,
            request_body={"prompt": "看[参考图片1]奔跑"},  # 入参 body
            result=None,
            node=node,
            status="ok",
            elapsed_ms=50,
        )
        clear_wire_audit()

    asyncio.run(_run())
    assert captured["prompt"] == "看【图片1】奔跑"
    stored = json.loads(captured["request_body_json"])
    assert stored["content"][0]["text"] == "看【图片1】奔跑"
    assert stored["model"] == "doubao-seedance-2-0"
    # 不应仍是调用方原始 prompt 为主
    assert captured["prompt"] != "看[参考图片1]奔跑"


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
