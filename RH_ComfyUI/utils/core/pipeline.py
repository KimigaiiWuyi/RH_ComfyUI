"""Pipeline / Node 注册表 — 全编程式节点定义

每个 NodeDef 描述一个节点(Node),包括:
- 身份(name, display_name, task_type, backend, backend_model)
- 能力(capabilities): 优先级、降级、输出 mime 等
- 输入输出(inputs, outputs): PortSpec 列表,提供类型校验与 UI 描述
- 映射规则(mode + mappings 或 mapper_func): 后端专属的"通用 → 厂商"转换

2026-07 起 NodeDef 全部由模型类的 node_def() 用代码构建
(models/*/defs.py 及外部插件),不再从 YAML 加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional
from dataclasses import field, dataclass

from .types import PortSpec, CapabilityManifest

if TYPE_CHECKING:
    from .request import TaskType


@dataclass
class NodeDef:
    """节点定义(由模型类的 node_def() 编程式构建)

    一个节点描述:
    1. 身份信息:name / display_name / task_type / backend / backend_model
    2. 能力声明:capabilities(优先级、降级链、输出 mime)
    3. 输入输出:inputs / outputs(typed ports,提供类型校验与 UI 描述)
    4. 执行方式:mode + mappings(declarative) 或 mapper_func(programmatic)
    """

    name: str
    display_name: str
    task_type: TaskType
    backend: str
    point_cost: int

    description: str = ""
    knowledge_content: str = ""
    requirements: list[str] = field(default_factory=list)

    # 工作流 / webapp id(ComfyUI / RH App 用)
    workflow_file: Optional[str] = None

    # 显式声明的厂商模型 ID(Seedance 等需要)
    backend_model: Optional[str] = None

    # 多供应商模型映射(provider_name -> vendor model id)
    # 当同一节点支持多家供应商时,key 为 provider name,value 为该家实际模型 ID;
    # 例: {ark: "doubao-seedance-2-0-260128", runninghub: ""}
    # 外部插件还可通过 seedance registry 的 register_vendor_models() 补充映射。
    backend_models: dict[str, str] = field(default_factory=dict)

    # 节点级供应商覆盖(可选):固定该节点走某家,忽略全局启用开关与负载均衡
    provider: Optional[str] = None

    # 映射
    mode: str = "declarative"  # declarative | programmatic
    mappings: dict[str, Any] = field(default_factory=dict)
    mapper_func: Optional[Callable[..., Any]] = None

    # 类型化端口
    inputs: dict[str, PortSpec] = field(default_factory=dict)
    outputs: dict[str, PortSpec] = field(default_factory=dict)

    # 能力声明
    capabilities: CapabilityManifest = field(default_factory=CapabilityManifest)


class PipelineRegistry:
    """节点注册表 — 由 discover_builtin_models() 与外部插件编程式装载

    对外暴露:
    - register(node) / get(name) / get_by_task(task_type) / all_pipelines()
    - find_by_partial_name(partial, task_type) — 模糊匹配
    """

    def __init__(self) -> None:
        self._pipelines: dict[str, NodeDef] = {}
        self._by_task: dict[TaskType, list[NodeDef]] = {}

    # ── 注册与查询 ──

    def register(self, node: NodeDef) -> None:
        from .request import TaskType

        self._pipelines[node.name] = node
        tasks = node.capabilities.supported_tasks if node.capabilities.supported_tasks else [node.task_type]
        for task in tasks:
            try:
                task_type = TaskType(task)
            except ValueError:
                continue
            bucket = self._by_task.setdefault(task_type, [])
            # 去重:同名节点重复注册时只保留最新一份
            bucket[:] = [n for n in bucket if n.name != node.name]
            bucket.append(node)

    def get(self, name: str) -> Optional[NodeDef]:
        return self._pipelines.get(name)

    def get_by_task(self, task_type: TaskType) -> list[NodeDef]:
        return self._by_task.get(task_type, [])

    def all_pipelines(self) -> list[NodeDef]:
        return list(self._pipelines.values())

    def find_by_partial_name(self, partial: str, task_type: TaskType) -> Optional[NodeDef]:
        """通过部分名称模糊匹配

        例如 "qwen" 可匹配 "qwen_2512","banana" 可匹配 "banana2"。
        返回当前任务类型下优先级最高的候选。
        """
        candidates = self.get_by_task(task_type)
        # 精确匹配
        for p in candidates:
            if partial == p.name:
                return p
        # 前缀匹配
        for p in candidates:
            if p.name.startswith(partial):
                return p
        # 包含匹配
        for p in candidates:
            if partial in p.name:
                return p
        return None


# 全局单例
pipeline_registry = PipelineRegistry()
