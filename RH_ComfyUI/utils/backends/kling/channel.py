"""KlingChannel — 可灵视频和图片共用一个通道名。

没启用、没凭证或未勾选该模型则不可用。在途请求占满则暂时不可用。
同一账号下的模型共用在途计数。
"""

from __future__ import annotations

from typing import Any

from .api import KlingAPI
from .config import api_key, access_key, max_concurrency, is_model_enabled, credentials_ready
from .payload import prepare
from ...audit_key import mask_api_key
from ...core.types import NodeOutput
from ....core.base.errors import ChannelError, GenerationError
from ....core.channels.channel import ChannelBinding, ProviderChannel
from ....core.channels.registry import channel_registry


class KlingRuntime:
    """全部可灵模型共享客户端和在途计数。"""

    def __init__(self) -> None:
        self.api = KlingAPI()
        self.inflight = 0


_RUNTIME = KlingRuntime()


class KlingChannel(ProviderChannel):
    """可灵官方通道。没有远程取消接口。"""

    name = "kling"
    weight = 5

    def __init__(self, model_name: str, runtime: KlingRuntime | None = None) -> None:
        self._model_name = model_name
        self._runtime = runtime if runtime is not None else _RUNTIME

    async def check_available(self) -> bool:
        # 只读配置和在途计数,禁止网络探测。
        configured = credentials_ready() and is_model_enabled(self._model_name)
        return configured and self._runtime.inflight < max_concurrency()

    async def unavailable_reason(self) -> str:
        if not credentials_ready():
            return "可灵未配置(需启用,并填写 API Key,或 Access Key + Secret Key)"
        if not is_model_enabled(self._model_name):
            return f"可灵未勾选该模型({self._model_name})"
        return "可灵并发已占满,请稍后重试"

    def audit_key_prefix(self) -> str:
        return mask_api_key(api_key() or access_key())

    def supports_remote_cancel(self) -> bool:
        return False

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request = kwargs["request"]
        submit = prepare(self._model_name, request)
        self._runtime.inflight += 1
        try:
            return await self._runtime.api.run(submit, model_name=self._model_name)
        except GenerationError:
            raise
        except Exception as exc:
            raise ChannelError(
                f"可灵 {self._model_name} 失败: {exc}",
                retryable=True,
                channel=self.name,
                user_message=f"可灵 {self._model_name} 失败,请稍后重试。",
            ) from exc
        finally:
            self._runtime.inflight -= 1


def bindings_for(model_name: str) -> list[ChannelBinding]:
    bindings = [ChannelBinding(KlingChannel(model_name), vendor_model=model_name)]
    bindings.extend(channel_registry.bindings_for(model_name))
    return bindings


__all__ = ["KlingChannel", "KlingRuntime", "bindings_for"]
