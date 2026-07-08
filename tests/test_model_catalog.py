"""/RH_ComfyUI/models 可用性回归 — 以模型通道为准,而非已删除的后端 Adapter

回归点:Seedance 改成多通道后不再有 Adapter,模型清单不能再报
"后端 seedance 未注册";可用性应反映(内置 + 外部注入的)通道。
"""

import asyncio
from typing import Any

from RH_ComfyUI.core import NodeOutput, ProviderChannel, channel_registry
from RH_ComfyUI.rh_models.api import _build_entry
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
