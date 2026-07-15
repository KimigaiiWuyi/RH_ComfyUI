"""Adapter 抽象层 — 所有 AIGC 后端的统一注册与访问"""

from __future__ import annotations

from typing import Dict, Optional

from .base import Adapter, Backend  # Backend 作为 Adapter 别名


class AdapterRegistry:
    """后端 / Adapter 注册表"""

    def __init__(self) -> None:
        self._adapters: Dict[str, Adapter] = {}
        # 旧 API 兼容：保存 AdapterRegistry 名称，便于旧代码访问
        self._backends: Dict[str, Adapter] = self._adapters

    def register(self, adapter: Adapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> Optional[Adapter]:
        return self._adapters.get(name)

    def all(self) -> list[Adapter]:
        """返回所有已注册的 Adapter"""
        return list(self._adapters.values())

    # ── 向后兼容旧 API ──

    def all_backends(self) -> list[Adapter]:
        """旧 API 兼容"""
        return self.all()


# 旧名兼容
class BackendRegistry(AdapterRegistry):
    pass


backend_registry = AdapterRegistry()


def init_backends() -> AdapterRegistry:
    """启动时注册所有 Adapter"""
    from .mimo.executor import MIMOAdapter
    from .rh_app.executor import RHAppAdapter
    from .comfyui.executor import ComfyUIAdapter
    from .minimax.executor import MiniMaxAdapter
    from .fishaudio.executor import FishAudioAdapter
    from .gpt_image2.executor import GPTImage2Adapter

    backend_registry.register(ComfyUIAdapter())
    backend_registry.register(RHAppAdapter())
    backend_registry.register(MiniMaxAdapter())
    backend_registry.register(MIMOAdapter())
    backend_registry.register(FishAudioAdapter())
    backend_registry.register(GPTImage2Adapter())
    # Seedance / Gemini 不是 Adapter:各供应商 = 一个 ProviderChannel,
    # 由通用 LoadBalancer 统一调度(见 seedance/channel.py、gemini_image/channel.py)。

    return backend_registry


__all__ = [
    "Adapter",
    "Backend",
    "AdapterRegistry",
    "BackendRegistry",
    "backend_registry",
    "init_backends",
]
