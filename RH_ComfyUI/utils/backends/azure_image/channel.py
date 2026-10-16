"""AzureImageChannel — gpt-image 家族的 Azure 通道。

没填凭证或未启用 → 不可用;该模型未勾选 → 不可用;
在途请求占满 → 暂时不可用。调用出错抛可重试 ChannelError,交给负载均衡。
同一资源下多个部署共用 ``AzureRuntime.inflight``。
"""

from __future__ import annotations

from typing import Any

from .api import (
    AzureGPTImage2API,
    max_concurrency,
    audit_key_prefix,
    credentials_ready,
    is_deployment_enabled,
)
from ...core.types import NodeOutput
from ...core.pipeline import NodeDef
from ....core.base.errors import ChannelError, GenerationError
from ....core.channels.channel import ProviderChannel


class AzureRuntime:
    """同一资源下多部署共享客户端与在途计数。"""

    def __init__(self) -> None:
        self.api = AzureGPTImage2API()
        self.inflight = 0


class AzureImageChannel(ProviderChannel):
    """Azure OpenAI 生图通道。"""

    name = "azure"
    # 直连 weight=3,高于默认 1。weighted 时本通道分到更多请求。
    weight = 3

    def __init__(self, *, model_name: str, runtime: AzureRuntime | None = None) -> None:
        self._model_name = model_name
        self._runtime = runtime if runtime is not None else AzureRuntime()

    def _configured(self) -> bool:
        return credentials_ready() and is_deployment_enabled(self._model_name)

    async def check_available(self) -> bool:
        # 只读判定:凭证齐全、该部署已勾选、未占满并发。禁止网络探测。
        return self._configured() and self._runtime.inflight < max_concurrency()

    async def unavailable_reason(self) -> str:
        if not credentials_ready():
            return "Azure 生图未配置(需启用 + API Key + 终结点)"
        if not is_deployment_enabled(self._model_name):
            return f"Azure 未勾选该部署({self._model_name})"
        return "Azure 生图并发已占满,请稍后重试"

    def audit_key_prefix(self) -> str:
        return audit_key_prefix()

    def supports_remote_cancel(self) -> bool:
        """单次 images 请求,没有可取消的上游任务。"""
        return False

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        request = kwargs["request"]
        node = kwargs["node"] if "node" in kwargs else None
        if not isinstance(node, NodeDef) or node.mapper_func is None:
            raise ChannelError("Azure 通道缺少 mapper", retryable=False, channel=self.name)

        self._runtime.inflight += 1
        try:
            self._runtime.api.refresh_config()
            result = await node.mapper_func(request, self._runtime.api)
        except GenerationError:
            # 参数类错误已带 retryable,不能改写成可切换通道。
            raise
        except Exception as exc:
            raise ChannelError(
                f"Azure {self._model_name} 失败: {exc}",
                retryable=True,
                channel=self.name,
                user_message=f"Azure {self._model_name} 生图失败,请稍后重试。",
            ) from exc
        finally:
            self._runtime.inflight -= 1

        if not isinstance(result, NodeOutput):
            raise ChannelError(
                f"Azure mapper 返回了非 NodeOutput: {type(result)}",
                retryable=False,
                channel=self.name,
            )
        return result


__all__ = ["AzureImageChannel", "AzureRuntime"]
