"""模型模块"""

from .types import (
    TaskType,
    ModelInfo,
    ModelStatus,
    ModelRequirement,
    ModelUnavailableError,
)
from .priority import MODEL_PRIORITY, _get_priority_model
from .registry import MODEL_REGISTRY
from .selector import recommend_model, generate_model_selection_prompt
from .availability import AvailabilityResult, availability_checker

__all__ = [
    # types
    "TaskType",
    "ModelInfo",
    "ModelStatus",
    "ModelRequirement",
    "ModelUnavailableError",
    # availability
    "availability_checker",
    "AvailabilityResult",
    # priority
    "MODEL_PRIORITY",
    "_get_priority_model",
    # registry
    "MODEL_REGISTRY",
    # selector
    "generate_model_selection_prompt",
    "recommend_model",
]
