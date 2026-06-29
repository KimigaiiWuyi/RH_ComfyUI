"""RH_ComfyUI — 顶层便捷导入。

外层 `__init__.py` 通过从内层 re-export 公开 API,
让 `from RH_ComfyUI import submit, is_available, ...` 直接可用。

实际包代码在内层 `RH_ComfyUI/RH_ComfyUI/`,符合
gscore-plugin-development 的"嵌套加载"规范。

> 这一层主要是为了 **跨插件调用**(如 canvas_backend 在另一插件中
> 通过 `from RH_ComfyUI.api import submit` 调用真实生成)。
> gsuid_core 的插件加载器把内层注册为 `RH_ComfyUI.RH_ComfyUI`,而
> 不在 `sys.path` 暴露顶层 `RH_ComfyUI` 包;所以必须在此处显式
> 透出符号才能被外部 import。
"""

from __future__ import annotations

# 公开 Python API(供 canvas_backend 等外部插件使用)
from .RH_ComfyUI.api import (  # noqa: F401
    GenerationResult,
    submit,
    list_models,
    is_available,
    get_point_cost,
)
from .RH_ComfyUI.utils.backends import (  # noqa: F401
    AdapterRegistry,
    init_backends,
    backend_registry,
)
from .RH_ComfyUI.utils.core.types import ProgressEvent  # noqa: F401

# 触发内层 __init__.py(执行 Plugins(...) + 注册命令 + @on_core_start 钩子)
from .RH_ComfyUI.utils.core.pipeline import NodeDef, pipeline_registry  # noqa: F401

__all__ = [
    # 公开 API
    "submit",
    "get_point_cost",
    "list_models",
    "is_available",
    "GenerationResult",
    "ProgressEvent",
    # 高级(允许需要写 Adapter / Pipeline 的下游插件访问内部注册表)
    "NodeDef",
    "pipeline_registry",
    "AdapterRegistry",
    "backend_registry",
    "init_backends",
]
