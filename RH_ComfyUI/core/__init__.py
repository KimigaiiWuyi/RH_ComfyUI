"""RH_ComfyUI.core — 生成引擎内核的稳定公开接口

闭源/外部插件只允许从本模块 import(不深入子模块),
未来内核重组不破坏外部代码。
"""

from .base import (
    ChannelError,
    VideoTaskShape,
    GenerationError,
    ValidationError,
    AIGCGenerationBase,
    BillingDeniedError,
    ImageGenerationBase,
    MusicGenerationBase,
    VideoGenerationBase,
    ModelUnavailableError,
    AllChannelsFailedError,
    DigitalHumanSpeechBase,
)
from .schema import (
    MediaRef,
    PortSpec,
    PortType,
    TaskType,
    ModelCard,
    NodeOutput,
    ContentItem,
    ProgressEvent,
    GenerationResult,
    GenerationRequest,
)
from .billing import (
    BillingPolicy,
    BillingContext,
    BillingReservation,
    PointsBillingPolicy,
    ExternalPrepaidPolicy,
)
from .routing import (
    LoadBalancer,
    route,
    model_registry,
    register_model,
    get_default_balancer,
    load_entry_point_models,
)
from .channels import LocalChannel, ChannelBinding, ProviderChannel
from .dispatch import DispatchContext, dispatch

__all__ = [
    # ABC 五件套
    "AIGCGenerationBase",
    "ImageGenerationBase",
    "VideoGenerationBase",
    "MusicGenerationBase",
    "DigitalHumanSpeechBase",
    "VideoTaskShape",
    # 类型
    "ModelCard",
    "PortSpec",
    "PortType",
    "MediaRef",
    "ContentItem",
    "NodeOutput",
    "ProgressEvent",
    "TaskType",
    "GenerationRequest",
    "GenerationResult",
    # 通道
    "ProviderChannel",
    "ChannelBinding",
    "LocalChannel",
    # 注册与路由
    "model_registry",
    "register_model",
    "load_entry_point_models",
    "route",
    "LoadBalancer",
    "get_default_balancer",
    # 计费与调度
    "BillingContext",
    "BillingReservation",
    "BillingPolicy",
    "PointsBillingPolicy",
    "ExternalPrepaidPolicy",
    "DispatchContext",
    "dispatch",
    # 错误族
    "GenerationError",
    "ValidationError",
    "ModelUnavailableError",
    "ChannelError",
    "AllChannelsFailedError",
    "BillingDeniedError",
]
