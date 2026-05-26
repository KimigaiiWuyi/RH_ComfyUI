"""核心架构层 — 统一请求模型、Pipeline注册表、智能路由、执行器"""

from .parser import parse_model_from_prompt
from .router import PRIORITY, ModelUnavailableError, route
from .request import TaskType, OutputType, GenerationResult, GenerationRequest
from .executor import execute_generation
from .pipeline import PipelineDef, PipelineRegistry, pipeline_registry

__all__ = [
    # request
    "GenerationRequest",
    "GenerationResult",
    "TaskType",
    "OutputType",
    # pipeline
    "PipelineDef",
    "PipelineRegistry",
    "pipeline_registry",
    # router
    "route",
    "ModelUnavailableError",
    "PRIORITY",
    # executor
    "execute_generation",
    # parser
    "parse_model_from_prompt",
]
