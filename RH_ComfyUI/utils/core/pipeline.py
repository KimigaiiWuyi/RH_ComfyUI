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

from typing import TYPE_CHECKING, Any, Callable, Optional, TypedDict
from dataclasses import field, dataclass

from .types import PortSpec, CapabilityManifest

if TYPE_CHECKING:
    from .request import TaskType


class MappingRule(TypedDict, total=False):
    """声明式映射规则(NodeDef.mappings 的元素)。

    键含义见 executor 的 _normalize_mappings / _resolve_mapping_value:
    source=取值来源路径, target='nodeId.field', value=直接常量,
    default=缺省值, type/template=上传或模板处理, optional=可缺省。
    """

    source: str
    target: str
    value: Any
    default: Any
    type: str
    template: str
    description: str
    optional: bool


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
    # 外部插件还可通过 channel_registry.register_binding() 追加自带 vendor_model 的通道。
    backend_models: dict[str, str] = field(default_factory=dict)

    # 节点级供应商家族(可选):只走该家族,实例名等于家族或以 {家族}_ 开头
    # 钉扎仍用 channel.name,不把家族 id 当成实例名
    provider: Optional[str] = None

    # 映射
    mode: str = "declarative"  # declarative | programmatic
    mappings: list[MappingRule] = field(default_factory=list)
    mapper_func: Optional[Callable[..., Any]] = None

    # 类型化端口
    inputs: dict[str, PortSpec] = field(default_factory=dict)
    outputs: dict[str, PortSpec] = field(default_factory=dict)

    # 能力声明
    capabilities: CapabilityManifest = field(default_factory=CapabilityManifest)

    # 目录分组(HTTP /models 与命令列表用)。None 时回退为 task_type.value。
    # 例:多角度 / 抠图等"专用工具"挂 catalog_group="tool",不进图片生成主列表。
    catalog_group: Optional[str] = None


class PipelineRegistry:
    """节点(NodeDef)目录 —— core.routing.model_registry 的派生只读视图。

    单一事实来源是 model_registry:凡注册进去的模型若带 `.node`(桥接/声明式
    模型),其 NodeDef 即出现在本目录,供 AI 知识库 / HTTP 契约 / 命令解析消费。
    如此外部插件用 register_model() 注册的模型会自动进目录,不再和 model_registry
    漂移。register() 仅为兼容极少数"直接登记裸 NodeDef"的历史调用保留。
    """

    def __init__(self) -> None:
        self._extra: dict[str, NodeDef] = {}

    def register(self, node: NodeDef) -> None:
        # 兼容路径:登记一个不经模型的 NodeDef(正常流程不会走到)
        self._extra[node.name] = node

    def _all_nodes(self) -> list[NodeDef]:
        from ...core.routing.registry import model_registry

        seen: dict[str, NodeDef] = {}
        for model in model_registry.all_models():
            if model.node is not None:
                seen[model.node.name] = model.node
        for name, node in self._extra.items():
            seen.setdefault(name, node)  # model_registry 优先
        return list(seen.values())

    def get(self, name: str) -> Optional[NodeDef]:
        from ...core.routing.registry import model_registry

        model = model_registry.get(name)
        if model is not None and model.node is not None:
            return model.node
        return self._extra.get(name)

    def get_by_task(self, task_type: TaskType) -> list[NodeDef]:
        from .request import TaskType

        out: list[NodeDef] = []
        for node in self._all_nodes():
            tasks = node.capabilities.supported_tasks if node.capabilities.supported_tasks else [node.task_type]
            for task in tasks:
                try:
                    tt = TaskType(task)
                except ValueError:
                    continue
                if tt == task_type:
                    out.append(node)
                    break
        return out

    def all_pipelines(self) -> list[NodeDef]:
        return self._all_nodes()

    def find_by_partial_name(self, partial: str, task_type: TaskType) -> Optional[NodeDef]:
        """精确 → 前缀 → 包含 三级模糊匹配(与 ModelRegistry 共用同一实现)"""
        from ...core.routing.registry import match_partial_name

        return match_partial_name(self.get_by_task(task_type), partial, lambda n: n.name)


# 全局单例
pipeline_registry = PipelineRegistry()
