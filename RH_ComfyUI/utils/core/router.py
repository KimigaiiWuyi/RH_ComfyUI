"""智能路由器 — 将 GenerationRequest 路由到最合适的 Pipeline"""

from __future__ import annotations

import random
from typing import Optional

from gsuid_core.logger import logger

from .request import TASK_DISPLAY_NAME, TaskType, GenerationRequest
from .pipeline import PipelineDef, PipelineRegistry, pipeline_registry


class ModelUnavailableError(Exception):
    """该任务类型没有可用模型"""

    def __init__(self, task_type: TaskType, reason: str) -> None:
        self.task_type = task_type
        self.reason = reason
        super().__init__(f"任务类型 {task_type.value} 无可用模型: {reason}")


# 优先级配置（可在 config 中覆盖）
PRIORITY: dict[TaskType, list[str]] = {
    TaskType.TEXT2IMAGE: ["qwen_2512", "banana2", "banana_pro", "anima"],
    TaskType.IMAGE2IMAGE: ["qwen_2512_img2img"],
    TaskType.IMAGE_EDIT: ["qwen_2511_edit", "banana2_edit", "banana_pro_edit"],
    TaskType.TEXT2VIDEO: ["wan2.2_text2video"],
    TaskType.IMAGE2VIDEO: ["wan2.2_img2video"],
    TaskType.MUSIC: ["ace_step1.5"],
    TaskType.SPEECH: ["IndexTTS2"],
}


async def route(
    request: GenerationRequest,
    registry: Optional[PipelineRegistry] = None,
) -> PipelineDef:
    """将 GenerationRequest 路由到最合适的 Pipeline

    策略顺序：
    1. 用户显式指定 model → 直接选
    2. 可用性过滤
    3. AI Agent 推荐（可选）
    4. 优先级兜底
    """
    if registry is None:
        registry = pipeline_registry
        _ensure_runtime_initialized(registry)

    # ── Step 1: 用户显式指定 ──
    if request.model:
        pipeline = registry.get(request.model)
        if pipeline and pipeline.task_type == request.task_type:
            from ..backends import backend_registry

            backend = backend_registry.get(pipeline.backend)
            if backend and await backend.check_available():
                logger.info(f"[Router] 用户指定模型: {request.model}")
                return pipeline
            logger.warning(f"[Router] 用户指定模型 {request.model} 不可用，回退自动选择")
        else:
            # 尝试模糊匹配
            pipeline = registry.find_by_partial_name(request.model, request.task_type)
            if pipeline:
                from ..backends import backend_registry

                backend = backend_registry.get(pipeline.backend)
                if backend and await backend.check_available():
                    logger.info(f"[Router] 模糊匹配模型: {request.model} → {pipeline.name}")
                    return pipeline
            logger.warning(f"[Router] 用户指定模型 {request.model} 不存在或任务类型不匹配")

    # ── Step 2: 可用性过滤 ──
    candidates = registry.get_by_task(request.task_type)
    available: list[PipelineDef] = []
    for p in candidates:
        from ..backends import backend_registry

        backend = backend_registry.get(p.backend)
        if backend and await backend.check_available():
            available.append(p)

    if not available:
        raise ModelUnavailableError(
            request.task_type,
            f"所有 {len(candidates)} 个 Pipeline 均不可用，请检查后端配置",
        )

    # 只有一个可用，直接选
    if len(available) == 1:
        logger.info(f"[Router] 唯一可用模型: {available[0].name}")
        return available[0]

    # ── Step 3: AI Agent 推荐 ──
    recommended = await _ai_recommend(request.prompt, request.task_type, available)
    if recommended:
        return recommended

    # ── Step 4: 优先级兜底 ──
    priority_list = PRIORITY.get(request.task_type, [])
    for name in priority_list:
        for p in available:
            if p.name == name:
                logger.info(f"[Router] 优先级选择: {p.name}")
                return p

    # 都没匹配到，随机选一个可用的
    selected = random.choice(available)
    logger.info(f"[Router] 随机选择 Pipeline: {selected.name}")
    return selected


def _ensure_runtime_initialized(registry: PipelineRegistry) -> None:
    """确保后端与 Pipeline 已初始化。

    正常情况下初始化由插件启动钩子完成；这里作为命令触发时的兜底，避免启动钩子未执行或热重载后
    注册表为空，导致提示“所有 0 个 Pipeline 均不可用”。
    """
    from ..backends import init_backends, backend_registry
    from ..resource.RESOURCE_PATH import PIPELINES_PATH, _CP_PIPELINES_PATH

    if not backend_registry.all_backends():
        init_backends()

    if registry.all_pipelines():
        return

    if _CP_PIPELINES_PATH.exists():
        registry.load_from_directory(_CP_PIPELINES_PATH)
    registry.load_from_directory(PIPELINES_PATH)

    logger.info(f"[Router] 懒加载 Pipeline 完成: {len(registry.all_pipelines())} 个")


async def _ai_recommend(prompt: str, task_type: TaskType, available: list[PipelineDef]) -> Optional[PipelineDef]:
    """使用 AI Agent 从可用 Pipeline 中推荐最合适的一个"""
    model_list = []
    for p in available:
        model_list.append(f"- **{p.name}**: {p.description}\n  详细说明：{p.knowledge_content}")

    task_name = TASK_DISPLAY_NAME.get(task_type, task_type.value)

    system_prompt = f"""你是一个专业的 AI 模型选择助手。

## 任务
根据用户的需求描述，从以下可用模型中选择最合适的一个。

## 用户需求
{prompt}

## 可用模型列表（{task_name}类别）
{chr(10).join(model_list)}

## 选择规则
1. 仔细分析用户需求的类型、风格、质量要求
2. 考虑各模型的特点和优势，选择最匹配的模型
3. 只返回一个模型名称，不要返回其他内容
"""

    try:
        from gsuid_core.ai_core.gs_agent import create_agent

        agent = create_agent(system_prompt=system_prompt, max_tokens=100)
        result = await agent.run(prompt)
        result = result.strip()

        for p in available:
            if p.name == result:
                logger.info(f"[Router] AI 推荐模型: {result}")
                return p

        logger.warning(f"[Router] AI 推荐结果无效: {result}")
    except Exception as e:
        logger.warning(f"[Router] AI 推荐失败: {e}")

    return None
