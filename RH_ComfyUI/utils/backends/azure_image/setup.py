"""把 Azure 通道绑到 gpt-image-2 / 2.5-sunburst / 2.5-flare。

是否真正选用由通道自己的 check_available 决定(凭证 + 部署勾选 + 并发)。
勾选热读。Enable / Deployment 变更走重挂。
"""

from __future__ import annotations

from .catalog import AZURE_IMAGE_MODEL_NAMES
from .channel import AzureRuntime, AzureImageChannel
from ....core.channels.resync import log_binding_diff, sync_owned_bindings

_REGISTERED: list[tuple[str, str]] = []


def register_azure_image() -> int:
    """绑到三个模型;可重复调用。无变化不打日志。"""
    global _REGISTERED
    runtime = AzureRuntime()
    desired: list[tuple[str, AzureImageChannel, str]] = [
        (name, AzureImageChannel(model_name=name, runtime=runtime), name) for name in AZURE_IMAGE_MODEL_NAMES
    ]
    _REGISTERED, diff = sync_owned_bindings(_REGISTERED, desired)
    log_binding_diff("[RHComfyUI] Azure 绑定", diff, len(_REGISTERED))
    return len(desired)
