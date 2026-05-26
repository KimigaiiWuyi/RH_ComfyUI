"""AI 知识库注册 — 将所有 Pipeline 信息注册为 AI 知识库"""

from __future__ import annotations

from gsuid_core.ai_core.models import KnowledgePoint
from gsuid_core.ai_core.register import ai_entity

from ..utils.core.pipeline import pipeline_registry


def register_pipeline_knowledge() -> None:
    """将所有 Pipeline 信息注册为 AI 知识库"""
    for p in pipeline_registry.all_pipelines():
        ai_entity(
            KnowledgePoint(
                id=f"rh_comfyui_pipeline_{p.name}",
                plugin="RH_ComfyUI",
                title=f"{p.display_name} - {p.task_type.value}",
                content=f"""
# {p.display_name}

## 基本信息
- 名称: {p.name}
- 任务类型: {p.task_type.value}
- 后端: {p.backend}
- 积分消耗: {p.point_cost}

## 描述
{p.description}

## 详细说明
{p.knowledge_content}

## 使用方式
命令: rh{p.task_type.value} {p.name} <描述>
AI: 在 ai_gen_* 工具中指定 model="{p.name}"
""",
                tags=["RH_ComfyUI", "AIGC", p.task_type.value, p.name, p.display_name],
            )
        )
