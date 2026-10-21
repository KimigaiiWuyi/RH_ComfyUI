"""MOSS Image:同一模型,ComfyUI 工作流与 RunningHub AI 应用两个供应商。"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path

from RH_ComfyUI.models.image.defs import (
    ALL_MODELS,
    MOSS_IMAGE_WIDTH,
    MOSS_IMAGE_APP_ID,
    MOSS_IMAGE_HEIGHT,
    MossImageDef,
)
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from gsuid_core.utils.plugins_config.models import GsListStrConfig
from RH_ComfyUI.utils.backends.rh_app.config import RH_APP_OPTIONS
from RH_ComfyUI.utils.backends.comfyui.config import COMFYUI_WORKFLOW_OPTIONS
from RH_ComfyUI.utils.backends.rh_app.executor import RHAppAdapter
from RH_ComfyUI.utils.backends.comfyui.executor import ComfyUIAdapter

_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / "RH_ComfyUI"
    / "utils"
    / "resource"
    / "workflow"
    / "图片生成"
    / "moss-image.json"
)


def test_moss_image_is_one_model_with_two_suppliers():
    assert MossImageDef in ALL_MODELS
    assert sum(1 for cls in ALL_MODELS if cls.__name__.startswith("MossImage")) == 1
    model = MossImageDef()
    local = model.node
    app = model._app_node
    assert local.name == "moss-image"
    assert app.name == "moss-image"
    assert local.backend == "comfyui"
    assert app.backend == "rh_app"
    assert local.workflow_file == "moss-image.json"
    assert app.workflow_file == MOSS_IMAGE_APP_ID
    assert app.backend_model == MOSS_IMAGE_APP_ID
    assert local.mode == "declarative"
    assert local.point_cost == 15
    assert app.point_cost == 15
    assert model.point_cost == 15
    assert model.execution_mode == "sync"
    assert app.capabilities.mode == "async_poll"
    for node in (local, app):
        assert node.task_type == TaskType("image")
        assert "images" not in node.inputs and "image" not in node.inputs
        assert node.inputs["prompt"].required is True
        assert node.inputs["width"].default == MOSS_IMAGE_WIDTH
        assert node.inputs["height"].default == MOSS_IMAGE_HEIGHT
    assert model.supports_edit is False
    assert model.supports_cancel is True
    assert model.supports_remote_cancel is True
    assert model._node_for("rh_app") is app
    assert model._node_for("comfyui") is local
    names = [binding.channel.name for binding in model.channel_bindings()]
    assert names.count("comfyui") == 1
    assert names.count("rh_app") == 1
    by_name = {binding.channel.name: binding for binding in model.channel_bindings()}
    assert by_name["comfyui"].vendor_model == "moss-image.json"
    assert by_name["rh_app"].vendor_model == MOSS_IMAGE_APP_ID
    assert by_name["comfyui"].channel.supports_remote_cancel() is True
    assert by_name["rh_app"].channel.supports_remote_cancel() is False


def test_moss_image_workflow_is_text_to_image():
    workflow = json.loads(_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["62"]["inputs"]["编辑文本"]
    assert workflow["7"]["inputs"]["width"] == MOSS_IMAGE_WIDTH
    assert workflow["7"]["inputs"]["height"] == MOSS_IMAGE_HEIGHT
    assert workflow["67"]["inputs"]["filename_prefix"] == "ComfyUI"
    class_types = {node["class_type"] for node in workflow.values()}
    assert "LoadImage" not in class_types
    assert "SaveImage" in class_types


def test_moss_image_comfyui_mapping_writes_prompt_and_size():
    adapter = ComfyUIAdapter()
    node = MossImageDef.node_def()
    resolved = adapter._resolve_workflow_path(node)
    assert resolved is not None and resolved.name == "moss-image.json"
    workflow = json.loads(_WORKFLOW.read_text(encoding="utf-8"))
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="原神中的早柚在海边跳舞",
        width=1200,
        height=1600,
    )
    out = asyncio.run(adapter._apply_declarative_mappings(req, node.mappings, workflow))
    assert out["62"]["inputs"]["编辑文本"] == "原神中的早柚在海边跳舞"
    assert out["7"]["inputs"]["width"] == 1200
    assert out["7"]["inputs"]["height"] == 1600
    assert out["5"]["inputs"]["text"] == ""


def test_moss_image_app_node_info_matches_runninghub_sample():
    adapter = RHAppAdapter()
    node = MossImageDef.app_node()
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="原神中的早柚在海边跳舞",
        width=1200,
        height=1600,
    )
    info = asyncio.run(adapter._build_node_info_list(req, node))
    by_field = {(item["nodeId"], item["fieldName"]): item for item in info}
    assert by_field[("62", "编辑文本")]["fieldValue"] == "原神中的早柚在海边跳舞"
    assert by_field[("62", "编辑文本")]["description"] == "编辑文本"
    assert by_field[("7", "width")]["fieldValue"] == "1200"
    assert by_field[("7", "width")]["description"] == "width"
    assert by_field[("7", "height")]["fieldValue"] == "1600"
    assert by_field[("7", "height")]["description"] == "height"
    assert len(info) == 3


def test_moss_image_enable_lists_use_one_name():
    assert "moss-image" in COMFYUI_WORKFLOW_OPTIONS
    assert "moss-image" in RH_APP_OPTIONS
    assert "moss-image-app" not in RH_APP_OPTIONS
    assert "moss-image-app" not in COMFYUI_WORKFLOW_OPTIONS
    workflows = SERVICE_CONFIG_DEFAULT["ComfyUI_Enabled_Workflows"]
    apps = SERVICE_CONFIG_DEFAULT["RH_App_Enabled_Apps"]
    assert isinstance(workflows, GsListStrConfig)
    assert isinstance(apps, GsListStrConfig)
    assert isinstance(workflows.data, list)
    assert isinstance(apps.data, list)
    assert "moss-image" in workflows.data
    assert workflows.options is not None and "moss-image" in workflows.options
    assert "moss-image" in apps.data
    assert apps.options is not None and "moss-image" in apps.options
    assert "moss-image-app" not in apps.data
    assert apps.options is not None and "moss-image-app" not in apps.options


def test_moss_image_aligns_cancel_with_supplier():
    from RH_ComfyUI.core.dispatch.active_tasks import get_active_task_registry

    async def _run() -> None:
        registry = get_active_task_registry()
        ag = await registry.register(
            model_name="moss-image",
            trace_id="moss-image-align-cancel",
            allow_cancel=True,
        )
        try:
            model = MossImageDef()
            assert registry.current() is ag
            model._align_cancel("rh_app")
            assert ag.allow_cancel is False
            model._align_cancel("comfyui")
            assert ag.allow_cancel is True
        finally:
            await registry.unregister(ag)

    asyncio.run(_run())
