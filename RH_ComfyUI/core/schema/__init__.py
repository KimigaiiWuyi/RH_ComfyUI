"""core.schema — 纯类型层,零业务逻辑"""

from .card import ModelCard
from .types import (
    MediaRef,
    PortSpec,
    PortType,
    MediaKind,
    NodeOutput,
    ContentItem,
    ProgressEvent,
    ContentItemType,
    ProgressCallback,
    audio_ref,
    image_ref,
    video_ref,
)
from .request import (
    TaskType,
    OutputType,
    GenerationResult,
    GenerationRequest,
)

__all__ = [
    "ModelCard",
    "PortSpec",
    "PortType",
    "MediaRef",
    "MediaKind",
    "ContentItem",
    "ContentItemType",
    "NodeOutput",
    "ProgressEvent",
    "ProgressCallback",
    "TaskType",
    "OutputType",
    "GenerationRequest",
    "GenerationResult",
    "image_ref",
    "video_ref",
    "audio_ref",
]
