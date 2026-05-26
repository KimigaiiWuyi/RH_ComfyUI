"""Pipeline 注册表 — 从 YAML 自动加载工作流定义"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Callable, Optional
from pathlib import Path
from dataclasses import dataclass

import yaml

from .request import TaskType


@dataclass
class PipelineDef:
    """Pipeline 定义（从 YAML 加载）

    一个 Pipeline 描述了：
    1. 身份信息：名称、描述、擅长什么
    2. 参数映射：从 GenerationRequest 提取哪些参数
    3. 执行方式：声明式映射 或 编程式映射函数
    """

    name: str
    display_name: str
    task_type: TaskType
    backend: str  # "comfyui" | "blt" | "rh"
    point_cost: int
    description: str
    knowledge_content: str
    requirements: list[str]
    workflow_file: Optional[str]  # ComfyUI 工作流 JSON 文件名
    mode: str  # "declarative" | "programmatic"
    mappings: dict  # 声明式映射规则
    mapper_func: Optional[Callable]  # 编程式映射函数
    yaml_path: Path  # YAML 文件路径（用于定位同目录的 workflow JSON）


class PipelineRegistry:
    """Pipeline 注册表 — 启动时从 YAML 自动构建"""

    def __init__(self) -> None:
        self._pipelines: dict[str, PipelineDef] = {}
        self._by_task: dict[TaskType, list[PipelineDef]] = {}

    def load_from_directory(self, base_path: Path) -> None:
        """递归扫描目录下所有 .yaml 文件，加载为 PipelineDef"""
        if not base_path.exists():
            return
        for yaml_file in base_path.rglob("*.yaml"):
            pipeline = self._load_yaml(yaml_file)
            self.register(pipeline)

    def _load_yaml(self, path: Path) -> PipelineDef:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        mapper_func: Optional[Callable] = None
        if data.get("mode") == "programmatic" and data.get("mapper"):
            module_path, func_name = data["mapper"].rsplit(":", 1)
            mod = self._import_mapper_module(module_path)
            mapper_func = getattr(mod, func_name)

        return PipelineDef(
            name=data["name"],
            display_name=data["display_name"],
            task_type=TaskType(data["task_type"]),
            backend=data["backend"],
            point_cost=data.get("point_cost", 2),
            description=data.get("description", ""),
            knowledge_content=data.get("knowledge_content", ""),
            requirements=data.get("requirements", []),
            workflow_file=data.get("workflow"),
            mode=data.get("mode", "declarative"),
            mappings=data.get("mappings", {}),
            mapper_func=mapper_func,
            yaml_path=path,
        )

    @staticmethod
    def _import_mapper_module(module_path: str) -> ModuleType:
        """导入 mapper 模块，兼容嵌套插件加载后的包名前缀。

        YAML 中历史上写的是 RH_ComfyUI.utils.xxx；在 GsCore 嵌套插件加载场景下，实际包名可能不是
        顶层 RH_ComfyUI，因此优先按 YAML 原路径导入，失败后改用相对导入。
        """
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if not module_path.startswith("RH_ComfyUI.utils."):
                raise exc
            relative_module = ".." + module_path.removeprefix("RH_ComfyUI.utils.")
            return importlib.import_module(relative_module, package=__package__)

    def register(self, pipeline: PipelineDef) -> None:
        self._pipelines[pipeline.name] = pipeline
        self._by_task.setdefault(pipeline.task_type, []).append(pipeline)

    def get(self, name: str) -> Optional[PipelineDef]:
        return self._pipelines.get(name)

    def get_by_task(self, task_type: TaskType) -> list[PipelineDef]:
        return self._by_task.get(task_type, [])

    def all_pipelines(self) -> list[PipelineDef]:
        return list(self._pipelines.values())

    def find_by_partial_name(self, partial: str, task_type: TaskType) -> Optional[PipelineDef]:
        """通过部分名称模糊匹配 Pipeline

        例如 "qwen" 可匹配 "qwen_2512"，"banana" 可匹配 "banana2"
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
