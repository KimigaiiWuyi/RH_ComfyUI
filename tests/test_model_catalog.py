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
    """无 NodeDef 的纯编程式模型(路径 C / 闭源接入形态)"""

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


def test_pure_abc_model_visible_in_catalog():
    # 回归:无 NodeDef 的模型(model.node is None)也必须进 HTTP 清单,
    # 否则闭源插件按 @register_model 注册的模型在画布上不可见
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
