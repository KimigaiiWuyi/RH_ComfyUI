"""后端抽象基类 — 所有 AIGC 后端必须实现此接口"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.request import GenerationResult, GenerationRequest
from ..core.pipeline import PipelineDef


class Backend(ABC):
    """后端抽象基类

    子类必须实现：
    - name: 后端唯一标识
    - check_available(): 检查后端是否可用
    - get_unavailable_reason(): 返回不可用原因
    - execute(): 执行生成任务
    """

    name: str

    @abstractmethod
    async def check_available(self) -> bool:
        """检查后端是否可用（配置是否完整、连接是否正常）"""
        ...

    @abstractmethod
    async def get_unavailable_reason(self) -> str:
        """如果不可用，返回原因描述"""
        ...

    @abstractmethod
    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        """执行生成任务

        Args:
            request: 统一生成请求
            pipeline: 选中的 Pipeline 定义（含映射规则/映射函数）

        Returns:
            统一生成结果
        """
        ...
