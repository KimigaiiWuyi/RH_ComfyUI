"""core.base — 抽象基类层(五个 ABC + 错误族 + 通用校验器)"""

from .image import ImageGenerationBase
from .music import MusicGenerationBase
from .video import VideoTaskShape, VideoGenerationBase
from .errors import (
    ChannelError,
    GenerationError,
    ValidationError,
    BillingDeniedError,
    ModelUnavailableError,
    AllChannelsFailedError,
)
from .speech import DigitalHumanSpeechBase
from .generation import AIGCGenerationBase
from .schema_validator import schema_supports_request, validate_against_schema

__all__ = [
    "AIGCGenerationBase",
    "ImageGenerationBase",
    "VideoGenerationBase",
    "MusicGenerationBase",
    "DigitalHumanSpeechBase",
    "VideoTaskShape",
    "GenerationError",
    "ValidationError",
    "ModelUnavailableError",
    "ChannelError",
    "AllChannelsFailedError",
    "BillingDeniedError",
    "validate_against_schema",
    "schema_supports_request",
]
