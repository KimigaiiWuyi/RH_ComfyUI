"""Seedance 内置供应商驱动 — ARK / RunningHub

外部供应商由独立插件用 SeedanceProviderChannel 包装自己的 SeedanceProvider,
经 ``channel_registry.register_binding()`` 注入宿主模型的候选通道,与内置供应商
共享通用 LoadBalancer 的负载均衡与熔断。
"""

from .ark import ArkSeedanceProvider
from .runninghub import RunningHubSeedanceProvider

__all__ = [
    "ArkSeedanceProvider",
    "RunningHubSeedanceProvider",
]
