"""命令解析器 — 从用户输入中提取可选模型名和实际 prompt"""

from __future__ import annotations

from typing import Optional

from gsuid_core.logger import logger

from .request import TaskType
from .pipeline import PipelineRegistry, pipeline_registry


def _ensure_registry_loaded(registry: PipelineRegistry) -> None:
    """确保 Pipeline 注册表已初始化（懒加载兜底）"""
    if registry.all_pipelines():
        return

    from ..backends import init_backends, backend_registry
    from ..resource.RESOURCE_PATH import PIPELINES_PATH, _CP_PIPELINES_PATH

    if not backend_registry.all_backends():
        init_backends()

    if _CP_PIPELINES_PATH.exists():
        registry.load_from_directory(_CP_PIPELINES_PATH)
    registry.load_from_directory(PIPELINES_PATH)

    logger.info(f"[Parser] 懒加载 Pipeline 完成: {len(registry.all_pipelines())} 个")


def parse_model_from_prompt(
    text: str,
    task_type: TaskType,
    registry: Optional[PipelineRegistry] = None,
) -> tuple[Optional[str], str]:
    """从用户输入中解析可选的模型名和实际 prompt

    解析规则：
    1. 提取第一个词，检查是否匹配已知 Pipeline 名
    2. 精确匹配 > 前缀匹配 > 包含匹配
    3. 不匹配则整个文本作为 prompt

    Args:
        text: 用户输入的原始文本（去掉命令关键词后）
        task_type: 当前任务类型
        registry: Pipeline 注册表（默认使用全局单例）

    Returns:
        (model_name_or_None, actual_prompt)
    """
    if registry is None:
        registry = pipeline_registry

    # 懒加载兜底：确保注册表已初始化
    _ensure_registry_loaded(registry)

    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None, ""

    first_word = parts[0].lower()

    # 尝试模糊匹配（大小写不敏感）
    pipeline = registry.find_by_partial_name(first_word, task_type)
    if pipeline:
        actual_prompt = parts[1] if len(parts) > 1 else ""
        logger.info(f"[Parser] 模型名解析成功: '{first_word}' -> {pipeline.name}, prompt={actual_prompt[:30]}...")
        return pipeline.name, actual_prompt

    # 不匹配任何模型名，整个文本作为 prompt
    # 调试：列出该任务类型的所有 Pipeline 名
    all_names = [p.name for p in registry.get_by_task(task_type)]
    logger.warning(f"[Parser] 模型名 '{first_word}' 未匹配，该任务类型可用模型: {all_names}")
    return None, text.strip()
