"""RH_ComfyUI.core — 生成引擎内核的稳定公开接口

闭源/外部插件只允许从本模块 import(不深入子模块),
未来内核重组不破坏外部代码。
"""

from .base import (
    ChannelError,
    VideoTaskShape,
    DryRunInterrupt,
    GenerationError,
    ValidationError,
    AsrGenerationBase,
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
from .channels import (
    LocalChannel,
    ChannelBinding,
    ProviderChannel,
    channel_registry,
    log_binding_diff,
    bind_config_resync,
    sync_owned_bindings,
    register_resync_hook,
    resync_channel_bindings,
)
from .dispatch import DispatchContext, dispatch, cancel_generation
from .media_host import (
    MediaPublisher,
    MediaPublishError,
    materialize as materialize_media,
    get_media_publisher,
    set_media_publisher,
)

# Seedance 供应商通道:外部插件(自带凭证面板)可复用本类把自己的
# SeedanceProvider 包装成通道,经 channel_registry 注入宿主模型的候选列表。
from ..utils.backends.seedance.channel import (
    ProviderCredentials,
    SeedanceProviderChannel,
)

__all__ = [
    # ABC 五件套
    "AIGCGenerationBase",
    "ImageGenerationBase",
    "VideoGenerationBase",
    "MusicGenerationBase",
    "DigitalHumanSpeechBase",
    "AsrGenerationBase",
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
    "channel_registry",
    "register_resync_hook",
    "resync_channel_bindings",
    "bind_config_resync",
    "sync_owned_bindings",
    "log_binding_diff",
    "ProviderCredentials",
    "SeedanceProviderChannel",
    # 媒体外链化扩展点(闭源/宿主注册 R2 等 publisher;开源零感知)
    "MediaPublisher",
    "MediaPublishError",
    "set_media_publisher",
    "get_media_publisher",
    "materialize_media",
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
    "cancel_generation",
    # 错误族
    "GenerationError",
    "ValidationError",
    "ModelUnavailableError",
    "ChannelError",
    "AllChannelsFailedError",
    "BillingDeniedError",
    "DryRunInterrupt",
]
