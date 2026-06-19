"""Pipeline / Node 注册表 — 从 YAML 自动加载节点定义

每个 YAML 描述一个节点(Node),包括:
- 身份(name, display_name, task_type, backend, backend_model)
- 能力(capabilities): 优先级、降级、输出 mime 等
- 输入输出(inputs, outputs): PortSpec 列表,提供类型校验与 UI 描述
- 映射规则(mode + mappings 或 mapper): 后端专属的"通用 → 厂商"转换

向后兼容:
- PipelineDef 作为 NodeDef 的别名导出
- 老 YAML 没有 inputs/outputs 时,自动从 GenerationRequest 字段推断
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Optional
from pathlib import Path
from dataclasses import field, dataclass

import yaml

from .types import PortSpec, PortType, CapabilityManifest

if TYPE_CHECKING:
    from .request import TaskType

# ═══════════════════════════════════════════════════════════════════════
#  字段名 → PortType 的推断映射(用于老 YAML 自动推断)
# ═══════════════════════════════════════════════════════════════════════

_FIELD_TO_PORT_TYPE: dict[str, PortType] = {
    "prompt": PortType.TEXT,
    "negative_prompt": PortType.TEXT,
    "text": PortType.TEXT,
    "width": PortType.INTEGER,
    "height": PortType.INTEGER,
    "duration": PortType.INTEGER,
    "seed": PortType.INTEGER,
    "ratio": PortType.ENUM,
    "resolution": PortType.ENUM,
    "generate_audio": PortType.BOOLEAN,
    "watermark": PortType.BOOLEAN,
    "camera_fixed": PortType.BOOLEAN,
    "return_last_frame": PortType.BOOLEAN,
    "service_tier": PortType.ENUM,
    "mood": PortType.STRING,
    "voice_id": PortType.STRING,
    "speed": PortType.NUMBER,
    "language_boost": PortType.STRING,
    "model": PortType.STRING,
    "images": PortType.LIST,
    "video_refs": PortType.LIST,
    "audio_refs": PortType.LIST,
    "ordered_content": PortType.CONTENT,
    "reference_audio": PortType.AUDIO,
}


# ═══════════════════════════════════════════════════════════════════════
#  NodeDef
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class NodeDef:
    """节点定义(从 YAML 加载)

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

    # 映射
    mode: str = "declarative"  # declarative | programmatic
    mappings: dict[str, Any] = field(default_factory=dict)
    mapper_func: Optional[Callable[..., Any]] = None
    yaml_path: Optional[Path] = None

    # 新增:类型化端口
    inputs: dict[str, PortSpec] = field(default_factory=dict)
    outputs: dict[str, PortSpec] = field(default_factory=dict)

    # 新增:能力声明
    capabilities: CapabilityManifest = field(default_factory=CapabilityManifest)

    # 兼容旧代码访问
    @property
    def yaml_path_for_workflow(self) -> Optional[Path]:
        return self.yaml_path


# 别名:向后兼容
PipelineDef = NodeDef


# ═══════════════════════════════════════════════════════════════════════
#  Pipeline / Node Registry
# ═══════════════════════════════════════════════════════════════════════


class PipelineRegistry:
    """节点注册表 — 启动时从 YAML 自动构建

    对外暴露:
    - get(name) / get_by_task(task_type) / all_pipelines()
    - find_by_partial_name(partial, task_type) — 模糊匹配
    """

    def __init__(self) -> None:
        self._pipelines: dict[str, NodeDef] = {}
        self._by_task: dict[TaskType, list[NodeDef]] = {}

    def load_from_directory(self, base_path: Path) -> None:
        """递归扫描目录下所有 .yaml 文件,加载为 NodeDef"""
        if not base_path.exists():
            return
        for yaml_file in base_path.rglob("*.yaml"):
            try:
                node = self._load_yaml(yaml_file)
            except Exception as e:
                # 不让单个 YAML 错误破坏整体启动
                from gsuid_core.logger import logger

                logger.exception(f"[PipelineRegistry] 加载 {yaml_file} 失败: {e}")
                continue
            self.register(node)

    def _load_yaml(self, path: Path) -> NodeDef:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        mapper_func: Optional[Callable[..., Any]] = None
        if data.get("mode") == "programmatic" and data.get("mapper"):
            module_path, func_name = data["mapper"].rsplit(":", 1)
            mod = self._import_mapper_module(module_path)
            mapper_func = getattr(mod, func_name)

        # ── inputs / outputs ──
        inputs = self._load_ports(data.get("inputs"))
        outputs = self._load_ports(data.get("outputs"))

        # 内部导入以避免 pipeline ↔ request ↔ types 模块加载阶段的循环
        from .request import TaskType, normalize_task_type

        # 归一化 task_type: 将旧式细分类型(image2image / text2image / image_edit 等)
        # 映射为当前枚举值(image / video / music / speech)
        raw_task_type = str(data.get("task_type", "image"))
        normalized_task_type = normalize_task_type(raw_task_type)

        # 如果没有显式声明 inputs,自动从 mappings.source 推断
        if not inputs:
            inputs = self._infer_inputs_from_mappings(data)

        # 默认 outputs(根据 task_type 推断)
        if not outputs:
            outputs = self._infer_default_outputs(TaskType(normalized_task_type))

        # ── capabilities ──
        capabilities = self._load_capabilities(data)

        return NodeDef(
            name=data["name"],
            display_name=data.get("display_name", data["name"]),
            task_type=TaskType(normalized_task_type),
            backend=data["backend"],
            point_cost=data.get("point_cost", 2),
            description=data.get("description", ""),
            knowledge_content=data.get("knowledge_content", ""),
            requirements=data.get("requirements", []) or [],
            workflow_file=data.get("workflow"),
            backend_model=data.get("backend_model"),
            mode=data.get("mode", "declarative"),
            mappings=data.get("mappings", {}) or {},
            mapper_func=mapper_func,
            yaml_path=path,
            inputs=inputs,
            outputs=outputs,
            capabilities=capabilities,
        )

    @staticmethod
    def _load_ports(data: Any) -> dict[str, PortSpec]:
        """加载 ports 配置,支持 dict-of-dict 与 list-of-dict 两种 YAML 写法"""
        if not data:
            return {}
        if isinstance(data, dict):
            return {
                name: PortSpec.from_dict(spec) if not isinstance(spec, PortSpec) else spec
                for name, spec in data.items()
            }
        if isinstance(data, list):
            ports: dict[str, PortSpec] = {}
            for item in data:
                if not isinstance(item, dict) or "name" not in item:
                    raise ValueError(f"port 列表项需要带 name 字段: {item}")
                name = item.pop("name")
                ports[name] = PortSpec.from_dict(item)
            return ports
        raise ValueError(f"ports 必须是 dict 或 list,得到 {type(data)}")

    @staticmethod
    def _infer_inputs_from_mappings(data: dict[str, Any]) -> dict[str, PortSpec]:
        """老 YAML: 没有显式 inputs,从 mappings.source 推断"""
        mappings = data.get("mappings") or {}
        inputs: dict[str, PortSpec] = {}
        # list 格式: [{source: ...}]
        if isinstance(mappings, list):
            for rule in mappings:
                source = rule.get("source")
                if not source:
                    continue
                # 支持 "images.0" 这种,只取根字段
                root = source.split(".", 1)[0]
                if root in _FIELD_TO_PORT_TYPE and root not in inputs:
                    inputs[root] = PortSpec(
                        type=_FIELD_TO_PORT_TYPE[root],
                        required=rule.get("required", False),
                        default=rule.get("default"),
                    )
            return inputs
        # dict 格式: {prompt: {...}}
        for source in mappings.keys():
            root = str(source).split(".", 1)[0]
            if root in _FIELD_TO_PORT_TYPE and root not in inputs:
                inputs[root] = PortSpec(type=_FIELD_TO_PORT_TYPE[root])
        return inputs

    @staticmethod
    def _infer_default_outputs(task_type: TaskType) -> dict[str, PortSpec]:
        """根据 task_type 推断默认 outputs"""
        from .request import TASK_OUTPUT_MAP

        ot = TASK_OUTPUT_MAP[task_type]
        if ot.value == "image":
            return {"image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片")}
        if ot.value == "video":
            return {"video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频")}
        return {"audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的音频")}

    @staticmethod
    def _load_capabilities(data: dict[str, Any]) -> CapabilityManifest:
        """加载 capabilities 块"""
        from .request import normalize_task_type

        cap: dict[str, Any] = data.get("capabilities") or {}
        # supported_tasks 类型为 list[str]，因此需要把可能的 None/Unknown 转为 str;
        # 同时归一化旧式类型(image2image 等)为当前枚举值(image 等)
        raw_supported_tasks = cap.get("supported_tasks") or [data.get("task_type")]
        supported_tasks: list[str] = [normalize_task_type(str(t)) for t in raw_supported_tasks if t is not None]
        return CapabilityManifest(
            supported_tasks=supported_tasks,
            supported_params=cap.get("supported_params") or [],
            output_mime=cap.get("output_mime") or [],
            mode=cap.get("mode", "sync"),
            priority=cap.get("priority", data.get("priority", 50)),
            fallback=cap.get("fallback") or data.get("fallback"),
            health_status="unknown",
        )

    @staticmethod
    def _import_mapper_module(module_path: str) -> ModuleType:
        """导入 mapper 模块,兼容多种包名前缀

        gsuid_core 嵌套加载插件后,模块可能被挂载到不同包名下:
          1. 原始路径:  RH_ComfyUI.utils.mappers.xxx
          2. 嵌套路径:  plugins.RH_ComfyUI.RH_ComfyUI.utils.mappers.xxx
        本方法依次尝试所有可能的前缀,直到成功导入。
        """
        # 尝试 1: 原始路径
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError:
            pass

        # 尝试 2: 相对导入(从当前包向上跳到 utils 级)
        if module_path.startswith("RH_ComfyUI.utils."):
            relative_module = ".." + module_path.removeprefix("RH_ComfyUI.utils.")
            try:
                return importlib.import_module(relative_module, package=__package__)
            except ModuleNotFoundError:
                pass

        # 尝试 3: plugins.RH_ComfyUI 前缀
        if not module_path.startswith("plugins."):
            plugins_path = "plugins.RH_ComfyUI." + module_path
            try:
                return importlib.import_module(plugins_path)
            except ModuleNotFoundError:
                pass

        # 所有尝试失败,抛出原始错误
        raise ModuleNotFoundError(f"无法导入 mapper 模块 {module_path!r} (也尝试了相对导入和 plugins.RH_ComfyUI 前缀)")

    # ── 查询 ──

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
            # 去重:同名节点(常见于内置路径 + 运行时路径被加载两次)只保留最新一份
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
