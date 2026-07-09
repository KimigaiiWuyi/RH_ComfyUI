"""openai_image — 通用 OpenAI 兼容生图供应商池(非某家专用)。

每家供应商 = 一个 OpenAIImageChannel(配置驱动凭证), 经 channel_registry 挂到现有模型上,
与内置通道一起负载均衡。百度千帆(/v2/images/generations)为首批可配供应商之一。
"""

from .channel import OpenAIImageChannel
from .providers import sync_openai_image_providers

__all__ = ["OpenAIImageChannel", "sync_openai_image_providers"]
