"""/api/RH_ComfyUI/models 可用性回归 — 以模型通道为准,而非已删除的后端 Adapter

回归点:Seedance 改成多通道后不再有 Adapter,模型清单不能再报
"后端 seedance 未注册";可用性应反映(内置 + 外部注入的)通道。
"""

import asyncio
from typing import Any, Optional

from RH_ComfyUI.core import (
    ModelCard,
    NodeOutput,
    ChannelBinding,
    ProviderChannel,
    ImageGenerationBase,
    channel_registry,
)
from RH_ComfyUI.rh_models.api import _build_entry, build_model_catalog
from RH_ComfyUI.core.schema.types import PortSpec, PortType
from RH_ComfyUI.models.video.defs import Seedance2Def
from RH_ComfyUI.core.routing.registry import model_registry


class _AvailChannel(ProviderChannel):
    name = "test-gateway"

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        return NodeOutput()


def test_seedance2_availability_from_channels_not_adapter():
    model = Seedance2Def()
    model_registry.register(model)
    node = model.node
    channel_registry.unregister("seedance2", "test-gateway")

    # 无供应商配置:不可用,但原因是"无可用供应商/通道",不是"后端未注册"
    entry = asyncio.run(_build_entry(node))
    assert entry.unavailable_reason != "后端 seedance 未注册"

    # 注入一路可用通道(等价于 aigc 配好网关 key)→ 模型转为可用
    channel_registry.register_binding("seedance2", _AvailChannel(), vendor_model="x")
    entry2 = asyncio.run(_build_entry(node))
    assert entry2.available is True
    assert "test-gateway" in [c["name"] for c in entry2.channels]

    channel_registry.unregister("seedance2", "test-gateway")


class _PureAbcModel(ImageGenerationBase):
    """无 NodeDef 的纯编程式模型(路径 C / 外部插件接入形态)"""

    name = "pure_abc_model"
    display_name = "Pure ABC"
    card = ModelCard(description="纯编程式测试模型")
    point_cost = 7

    def input_schema(self) -> dict[str, PortSpec]:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True, description="描述")}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(_AvailChannel())]

    async def execute_on_channel(
        self, request: Any, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        return await binding.channel.invoke(request=request)


def test_catalog_cancel_flags_match_channels_and_rh_app():
    """/models 取消能力:顶层 = 通道 OR;rh_app 顶层与通道均为 false(HTTP 契约)。"""
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.utils.backends import init_backends

    init_backends()
    discover_builtin_models()
    try:
        catalog = asyncio.run(build_model_catalog(include_unavailable=True))
        by_name = {m["name"]: m for m in catalog["models"]}

        # rh_app:不能取消
        for name in ("anima", "rh_camera_angle"):
            if name not in by_name:
                continue
            m = by_name[name]
            assert m["supports_cancel"] is False, name
            assert m["supports_remote_cancel"] is False, name
            for ch in m.get("channels") or []:
                assert ch.get("supports_cancel") is False, (name, ch)
                assert ch.get("supports_remote_cancel") is False, (name, ch)

        # seedance:顶层与通道 OR 一致;ark 可 remote,runninghub 视频端不可
        if "seedance2" in by_name:
            m = by_name["seedance2"]
            ch_map = {c["name"]: c for c in (m.get("channels") or [])}
            any_local = any(c.get("supports_cancel") for c in ch_map.values())
            any_remote = any(c.get("supports_remote_cancel") for c in ch_map.values())
            assert m["supports_cancel"] is any_local
            assert m["supports_remote_cancel"] is any_remote
            if "ark" in ch_map:
                assert ch_map["ark"].get("supports_remote_cancel") is True
            if "runninghub" in ch_map:
                assert ch_map["runninghub"].get("supports_remote_cancel") is False
    finally:
        model_registry.clear()


def test_pure_abc_model_visible_in_catalog():
    # 回归:无 NodeDef 的模型(model.node is None)也必须进 HTTP 清单,
    # 否则外部插件按 @register_model 注册的模型在 HTTP 清单上不可见
    model = _PureAbcModel()
    model_registry.register(model)
    try:
        catalog = asyncio.run(build_model_catalog(include_unavailable=True))
        by_name = {m["name"]: m for m in catalog["models"]}
        assert "pure_abc_model" in by_name
        entry = by_name["pure_abc_model"]
        assert entry["available"] is True
        assert entry["point_cost"] == 7
        assert "prompt" in entry["input_schema"]
        assert entry["task_type"] == "image"
    finally:
        model_registry.unregister(model.name)
