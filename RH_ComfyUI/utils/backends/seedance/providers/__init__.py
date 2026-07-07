"""Seedance 内置供应商驱动 — ARK / RunningHub

外部供应商(如聚合网关)由独立插件(aigc_system)通过
``registry.register_provider()`` 注入,与内置供应商共享负载均衡与熔断。
"""

from .ark import ArkSeedanceProvider
from .runninghub import RunningHubSeedanceProvider

__all__ = [
    "ArkSeedanceProvider",
    "RunningHubSeedanceProvider",
]
