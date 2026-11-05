"""core.channels — 供应商无关的通道抽象"""

from .family import channel_name_in_family, lock_bindings_to_provider_family
from .health import AttemptLease, CircuitState, ChannelHealth
from .resync import (
    log_binding_diff,
    bind_config_resync,
    sync_owned_bindings,
    register_resync_hook,
    resync_channel_bindings,
)
from .channel import LocalChannel, ChannelBinding, ProviderChannel
from .failure import Phase, FailureKind, classify_transport, classify_http_status, http_status_retryable
from .polling import PollErrorTolerance, PollingChannelMixin
from .registry import ChannelExtensionRegistry, channel_registry

__all__ = [
    "ProviderChannel",
    "ChannelBinding",
    "LocalChannel",
    "channel_name_in_family",
    "lock_bindings_to_provider_family",
    "PollingChannelMixin",
    "PollErrorTolerance",
    "Phase",
    "FailureKind",
    "classify_http_status",
    "classify_transport",
    "http_status_retryable",
    "AttemptLease",
    "ChannelHealth",
    "CircuitState",
    "channel_registry",
    "ChannelExtensionRegistry",
    "register_resync_hook",
    "resync_channel_bindings",
    "bind_config_resync",
    "sync_owned_bindings",
    "log_binding_diff",
]
