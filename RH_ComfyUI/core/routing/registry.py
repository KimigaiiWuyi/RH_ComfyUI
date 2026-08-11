"""ModelRegistry — 模型注册表(开源/闭源统一扩展点)

注册途径(按加载顺序):
1. 开源内置:models/__init__.py 的 discover_builtin_models() 在 on_core_start
   时装载(YAML 桥接模型 + 编程式模型类),import/调用即注册。
2. Python entry points:pip 包在 pyproject.toml 声明
   [project.entry-points."rh_comfyui.models"],启动时自动加载(可选途径)。
3. 外部插件直接调用:另外的兼容插件生态在自己的 @on_core_start 里 import 本模块并调
   register_model()。注册表接受任意时点注册。

去重规则:同 name 后注册者覆盖先注册者并打 warning(允许另外的兼容插件生态覆盖开源
同名模型,例如把开源 seedance2 换成走内部网关的版本)。
"""

from __future__ import annotations

import threading
from typing import TypeVar, Callable, Optional, Sequence

from gsuid_core.logger import logger

from ..schema.request import TaskType
from ..base.generation import AIGCGenerationBase

T = TypeVar("T", bound=AIGCGenerationBase)
_N = TypeVar("_N")


def match_partial_name(candidates: Sequence[_N], partial: str, name_of: Callable[[_N], str]) -> Optional[_N]:
    """精确 → 前缀 → 包含 三级模糊匹配(ModelRegistry / PipelineRegistry 共用)"""
    for c in candidates:
        if partial == name_of(c):
            return c
    for c in candidates:
        if name_of(c).startswith(partial):
            return c
    for c in candidates:
        if partial in name_of(c):
            return c
    return None


class ModelRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict[str, AIGCGenerationBase] = {}

    def register(self, model: AIGCGenerationBase) -> None:
        with self._lock:
            if model.name in self._models:
                logger.warning(
                    f"[ModelRegistry] 模型 {model.name} 被重复注册,"
                    f"新实现 {type(model).__module__}.{type(model).__qualname__} 覆盖旧实现"
                )
            self._models[model.name] = model
        logger.info(f"[ModelRegistry] 注册模型: {model.name} ({model.display_name})")

    def unregister(self, name: str) -> None:
        with self._lock:
            self._models.pop(name, None)

    def clear(self) -> None:
        """测试用:清空注册表"""
        with self._lock:
            self._models.clear()

    def get(self, name: str) -> Optional[AIGCGenerationBase]:
        return self._models.get(name)

    def by_modality(self, modality: TaskType) -> list[AIGCGenerationBase]:
        return [m for m in self._models.values() if m.modality == modality]

    def all_models(self) -> list[AIGCGenerationBase]:
        return list(self._models.values())

    def find_by_partial_name(self, partial: str, modality: TaskType) -> Optional[AIGCGenerationBase]:
        """精确 → 前缀 → 包含 三级模糊匹配"""
        return match_partial_name(self.by_modality(modality), partial, lambda m: m.name)


model_registry = ModelRegistry()


def register_model(cls: type[T]) -> type[T]:
    """类装饰器:实例化并注册(模型类必须可无参构造)"""
    model_registry.register(cls())
    return cls


def load_entry_point_models() -> int:
    """加载 pip 包通过 entry points 提供的模型(闭源接入途径之一)"""
    from importlib.metadata import entry_points

    count = 0
    for ep in entry_points(group="rh_comfyui.models"):
        try:
            obj = ep.load()  # 模块(import 即注册)或可调用(返回模型类列表)
        except Exception as e:  # noqa: BLE001 — 单个外部包损坏不拖垮启动
            logger.warning(f"[ModelRegistry] entry point {ep.name} 加载失败: {e}")
            continue
        if callable(obj) and not isinstance(obj, type):
            produced = obj()  # 工厂:应返回模型类列表
            if not isinstance(produced, list):
                logger.warning(f"[ModelRegistry] entry point {ep.name} 工厂未返回列表,跳过")
                continue
            for model_cls in produced:
                if isinstance(model_cls, type) and issubclass(model_cls, AIGCGenerationBase):
                    model_registry.register(model_cls())
                    count += 1
        else:
            count += 1
    return count


__all__ = [
    "ModelRegistry",
    "model_registry",
    "register_model",
    "load_entry_point_models",
    "match_partial_name",
]
