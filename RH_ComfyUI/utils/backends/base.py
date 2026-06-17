"""Adapter 抽象基类 — 所有 AIGC 后端的统一接口

继承自 Adapter(子类) 必须实现:
- name: 后端唯一标识
- check_available(): 检查后端是否可用(配置是否完整、连接是否正常)
- get_unavailable_reason(): 返回不可用原因
- capabilities(): 声明该后端能处理哪些任务、消费哪些参数
- execute(): 执行生成任务,支持 progress_callback 异步上报

设计要点:
1. 能力声明驱动 — Adapter 自报家门,Router 不需要硬编码任何后端列表
2. progress_callback — 异步长任务(尤其视频)的统一进度上报通道
3. 兼容旧 Backend — Backend 作为 Adapter 的别名,保留旧代码
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable, Optional, Awaitable

from ..core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ..core.request import GenerationResult, GenerationRequest

if TYPE_CHECKING:
    from ..core.pipeline import NodeDef


# 进度回调类型(可选)
ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


class Adapter(ABC):
    """AIGC 后端适配器基类

    子类必须实现 `name`、`check_available`、`get_unavailable_reason`、
    `capabilities` 与 `execute`。
    """

    name: str

    @abstractmethod
    async def check_available(self) -> bool:
        """检查后端是否可用(配置是否完整、连接是否正常)"""
        ...

    @abstractmethod
    async def get_unavailable_reason(self) -> str:
        """如果不可用,返回原因描述"""
        ...

    @abstractmethod
    def capabilities(self) -> CapabilityManifest:
        """声明该后端的能力(任务类型、消费参数、输出格式等)

        该声明驱动 Router 进行能力匹配与优先级排序。
        """
        ...

    @abstractmethod
    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        """执行生成任务

        Args:
            request: 统一生成请求
            node: 选中的节点定义(含映射规则/映射函数/端口规格/能力)
            on_progress: 可选的进度回调(视频等长任务使用)

        Returns:
            NodeOutput: 统一节点输出,旧 GenerationResult 通过 .to_result() 兼容
        """
        ...

    # ── 便捷方法(向后兼容) ──

    async def execute_legacy(
        self,
        request: GenerationRequest,
        node: NodeDef,
    ) -> GenerationResult:
        """兼容旧调用:execute() → GenerationResult

        通过 ``GenerationResult.from_node_output`` 在 ``request.py`` 中进行转换,
        避免 ``utils.core.types`` 与 ``utils.core.request`` 之间的循环依赖。
        """
        out = await self.execute(request, node)
        return GenerationResult.from_node_output(out)


# ── 向后兼容别名 ──

Backend = Adapter


__all__ = [
    "Adapter",
    "Backend",
    "ProgressCallback",
    "CapabilityManifest",
    "NodeOutput",
    "ProgressEvent",
]
