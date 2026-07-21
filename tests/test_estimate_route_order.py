"""端到端:用 TestClient 实际请求 /models/estimate,验证不被 {task_type} 通配吃掉。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gsuid_core.app_life import app
from RH_ComfyUI.rh_models import webapi  # noqa: F401  确保路由已挂载
from RH_ComfyUI.models import discover_builtin_models
from RH_ComfyUI.utils.backends import init_backends
from RH_ComfyUI.core.routing.registry import model_registry


@pytest.fixture
def client():
    init_backends()
    discover_builtin_models()
    yield TestClient(app)
    model_registry.clear()


def test_estimate_endpoint_returns_dynamic_cost(client):
    """GET /models/estimate 必须返回积分估算,而不是"未知任务类型"错误"""
    r = client.get(
        "/api/RH_ComfyUI/models/estimate"
        "?model=gpt-image-2&ratio=4:3&image_size=2K"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "error" not in body, f"estimate 被路由吃掉了:{body}"
    assert body["model"] == "gpt-image-2"
    assert body["point_cost"] > 0
    assert body["is_dynamic"] is True


def test_task_type_endpoint_still_works(client):
    """回归:修复不影响 /models/image 这种正常按任务类型查询的路径"""
    r = client.get("/api/RH_ComfyUI/models/image")
    assert r.status_code == 200
    body = r.json()
    assert "task_types" in body
    assert "image" in body["task_types"]