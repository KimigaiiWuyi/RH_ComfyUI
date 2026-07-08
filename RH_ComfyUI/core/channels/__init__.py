"""core.channels — 供应商无关的通道抽象"""

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
]
