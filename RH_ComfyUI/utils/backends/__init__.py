"""后端抽象层 — 所有 AIGC 后端的统一注册与访问"""

from __future__ import annotations

from typing import Dict, Optional

from .base import Backend


class BackendRegistry:
    """后端注册表"""

    def __init__(self) -> None:
        self._backends: Dict[str, Backend] = {}

    def register(self, backend: Backend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> Optional[Backend]:
        return self._backends.get(name)

    def all_backends(self) -> list[Backend]:
        return list(self._backends.values())


# 全局单例
backend_registry = BackendRegistry()


def init_backends() -> BackendRegistry:
    """启动时注册所有后端"""
    from .blt.executor import BLTBackend
    from .mimo.executor import MIMOBackend
    from .rh_app.executor import RHAppBackend
    from .comfyui.executor import ComfyUIBackend
    from .minimax.executor import MiniMaxBackend

    backend_registry.register(ComfyUIBackend())
    backend_registry.register(BLTBackend())
    backend_registry.register(RHAppBackend())
    backend_registry.register(MiniMaxBackend())
    backend_registry.register(MIMOBackend())

    return backend_registry


__all__ = [
    "Backend",
    "BackendRegistry",
    "backend_registry",
    "init_backends",
]
