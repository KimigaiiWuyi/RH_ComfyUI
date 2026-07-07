"""ProviderChannel — 供应商通道抽象(全模态通用)

与旧 SeedanceProvider 的关系:
- SeedanceProvider 是"视频领域、细粒度(render/parse/poll)"的供应商抽象,保留;
- ProviderChannel 是"全模态、粗粒度(invoke)"的通道抽象;
- 模型侧可用薄包装把前者适配成后者。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass

from ..schema.types import NodeOutput


class ProviderChannel(ABC):
    """通道:凭证管理 + 可用性 + 执行"""

    name: str = ""

    # 权重(weighted 负载均衡策略使用;官方直连通道设高,代理通道设低)
    weight: int = 1

    @abstractmethod
    async def check_available(self) -> bool:
        """通道是否可用(只读配置,禁止网络探测)"""

    async def unavailable_reason(self) -> str:
        return f"通道 {self.name} 不可用(缺少配置)"

    @abstractmethod
    async def invoke(self, **kwargs: Any) -> NodeOutput:
        """执行一次生成;参数由所属模型的 execute_on_channel 约定

        实现方须把上游错误翻译为 ChannelError(retryable 标注是否可切换通道)。
        """


@dataclass
class ChannelBinding:
    """模型 × 通道绑定;vendor_model 为该通道下的厂商模型 ID"""

    channel: ProviderChannel
    vendor_model: Optional[str] = None


class LocalChannel(ProviderChannel):
    """本地直连通道(单通道模型用)

    典型用法:ComfyUI 类模型只有一条执行路径,不需要真正的多通道 invoke,
    模型的 execute_on_channel 直接调 backends 客户端;LocalChannel 仅提供
    name/weight 让负载均衡与统计的口径统一(channel 字段总有值)。
    """

    def __init__(self, name: str, *, required_config: Optional[list[str]] = None) -> None:
        self.name = name
        self._required_config = required_config or []

    async def check_available(self) -> bool:
        if not self._required_config:
            return True
        from ...rh_config.comfyui_config import SERVICE_CONFIG

        return all(SERVICE_CONFIG.get_config(k).data for k in self._required_config)

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        raise NotImplementedError("LocalChannel 由模型 execute_on_channel 直接执行")


__all__ = ["ProviderChannel", "ChannelBinding", "LocalChannel"]
