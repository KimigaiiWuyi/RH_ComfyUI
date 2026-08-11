"""core.schema.request — 请求/结果模型的规范导入路径(re-export)"""

from __future__ import annotations

from ...utils.core.request import (
    TASK_MIME_MAP,
    TASK_OUTPUT_MAP,
    TASK_DISPLAY_NAME,
    CATALOG_GROUP_DISPLAY,
    TaskType,
    OutputType,
    GenerationResult,
    GenerationRequest,
    normalize_task_type,
)

__all__ = [
    "TaskType",
    "OutputType",
    "GenerationRequest",
    "GenerationResult",
    "TASK_OUTPUT_MAP",
    "TASK_MIME_MAP",
    "TASK_DISPLAY_NAME",
    "CATALOG_GROUP_DISPLAY",
    "normalize_task_type",
]
