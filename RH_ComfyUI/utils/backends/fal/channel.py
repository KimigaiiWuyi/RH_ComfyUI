"""fal 通道包装:配置可用性 + invoke。

FalChannel 是 ProviderChannel;模型侧 channel_bindings() 把
模型 → (fal) 通道绑定交给 LB。并发计数在 FalRuntime 全局单例里。
"""

from __future__ import annotations

from typing import Optional
from dataclasses import field, dataclass

from . import config
from .api import FalAPI
from .catalog import app_prefix
from .payload import prepare
from ...audit_key import mask_api_key
from ....core.base.errors import ChannelError, GenerationError
from ....utils.core.types import NodeOutput
from ....core.schema.request import GenerationRequest
from ....core.channels.channel import ChannelBinding, ProviderChannel
from ....core.channels.failure import Phase, FailureKind
from ....core.channels.registry import channel_registry


@dataclass
class FalRuntime:
    """fal 客户端与在途计数。配置热读,客户端本身无状态。"""

    api: FalAPI = field(default_factory=FalAPI)
    inflight: int = 0


_RUNTIME = FalRuntime()


class FalChannel(ProviderChannel):
    """fal 直连通道(官方)。"""

    name = "fal"
    weight = 5

    def __init__(self, model_name: str, runtime: Optional[FalRuntime] = None) -> None:
        self._model_name = model_name
        self._runtime = runtime or _RUNTIME

    async def check_available(self) -> bool:
        return (
            config.credentials_ready()
            and config.is_model_enabled(self._model_name)
            and self._runtime.inflight < config.max_concurrency()
        )

    async def unavailable_reason(self) -> str:
        if not config.enabled_flag():
            return "FAL_Enable 未打开"
        if not config.api_key():
            return "缺少 FAL_API_Key"
        if not config.is_model_enabled(self._model_name):
            return f"模型 {self._model_name} 不在 FAL_Enabled_Models"
        return "fal 并发已满"

    def audit_key_prefix(self) -> str:
        return mask_api_key(config.api_key())

    def supports_remote_cancel(self) -> bool:
        return True

    async def invoke(self, *, request: GenerationRequest, **_: object) -> NodeOutput:
        submit = prepare(self._model_name, request)
        self._runtime.inflight += 1
        try:
            return await self._runtime.api.run(submit, model_name=self._model_name)
        except GenerationError:
            raise
        except Exception as exc:
            raise ChannelError(
                f"fal 执行失败:{exc}",
                kind=FailureKind.PROVIDER_FAULT,
                phase=Phase.AMBIGUOUS,
                channel=self.name,
            ) from exc
        finally:
            self._runtime.inflight -= 1


def bindings_for(model_name: str) -> list[ChannelBinding]:
    """模型 → fal 直连 + 调用方注入的扩展通道。"""
    return [
        ChannelBinding(FalChannel(model_name), vendor_model=app_prefix(model_name))
    ] + channel_registry.bindings_for(model_name)


__all__ = ["FalChannel", "FalRuntime", "bindings_for"]
