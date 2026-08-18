"""core.channels — 供应商无关的通道抽象"""

from .resync import (
    log_binding_diff,
    bind_config_resync,
    sync_owned_bindings,
    register_resync_hook,
    resync_channel_bindings,
)
from .channel import LocalChannel, ChannelBinding, ProviderChannel
from .polling import PollingChannelMixin
from .registry import ChannelExtensionRegistry, channel_registry

__all__ = [
    "ProviderChannel",
    "ChannelBinding",
    "LocalChannel",
    "PollingChannelMixin",
    "channel_registry",
    "ChannelExtensionRegistry",
    "register_resync_hook",
    "resync_channel_bindings",
    "bind_config_resync",
    "sync_owned_bindings",
    "log_binding_diff",
]
