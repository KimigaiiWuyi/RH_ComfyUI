"""核心架构层 — 统一请求模型、Pipeline注册表、智能路由、执行器"""

from .types import (
    MediaRef,
    PortSpec,
    PortType,
    MediaKind,
    NodeOutput,
    ContentItem,
    ProgressEvent,
    ContentItemType,
    CapabilityManifest,
)
from .parser import parse_model_from_prompt
from .router import ModelUnavailableError, route
from .request import TaskType, OutputType, GenerationResult, GenerationRequest
from .executor import execute_generation
from .pipeline import NodeDef, PipelineRegistry, pipeline_registry

__all__ = [
    # request
    "GenerationRequest",
    "GenerationResult",
    "TaskType",
    "OutputType",
    # pipeline
    "NodeDef",
    "PipelineRegistry",
    "pipeline_registry",
    # router
    "route",
    "ModelUnavailableError",
    # executor
    "execute_generation",
    # parser
    "parse_model_from_prompt",
    # types
    "PortType",
    "PortSpec",
    "MediaKind",
    "MediaRef",
    "ContentItemType",
    "ContentItem",
    "CapabilityManifest",
    "ProgressEvent",
    "NodeOutput",
]
