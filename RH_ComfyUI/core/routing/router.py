"""route() — 模型路由(所有入口共用)

五级策略(平移旧 utils/core/router.py,数据源换成 model_registry):
1. 用户显式 model → 精确匹配;失败则同模态内模糊匹配 + supports() 过滤
2. 输入档案匹配   → by_modality(task_type) 内 supports(request) 过滤
3. 可用性过滤     → 逐个 await model.check_available()(纯配置读取)
4. AI Agent 推荐  → gs_agent.recommend_model 钩子(可选,失败静默降级)
5. 优先级 + 随机  → priority 最高组内随机
"""

from __future__ import annotations

import random
from typing import Any, Optional

from gsuid_core.logger import logger

from .registry import ModelRegistry, model_registry
from ..base.errors import ModelUnavailableError
from ..schema.request import GenerationRequest
from ..base.generation import AIGCGenerationBase


async def _collect_unavailable_reasons(candidates: list[AIGCGenerationBase]) -> str:
    reasons: list[str] = []
    for m in candidates:
        reasons.append(f"- {await m.unavailable_reason()}")
    return "\n".join(reasons)


async def route(
    request: GenerationRequest,
    registry: Optional[ModelRegistry] = None,
) -> AIGCGenerationBase:
    """将 GenerationRequest 路由到最合适的模型实例"""
    if registry is None:
        registry = model_registry

    # ── 1. 用户显式指定 model ──
    if request.model:
        model = registry.get(request.model)
        if model is not None and model.modality == request.task_type and model.supports(request):
            if await model.check_available():
                logger.info(f"[Router] 用户指定模型: {model.name}")
                return model
            logger.warning(f"[Router] 用户指定模型 {model.name} 不可用,回退自动选择")
        else:
            partial = request.model.lower()
            matches = [
                m
                for m in registry.by_modality(request.task_type)
                if partial in m.name.lower() and m.supports(request)
            ]
            matches.sort(key=lambda m: m.priority, reverse=True)
            for m in matches:
                if await m.check_available():
                    logger.info(f"[Router] 模糊匹配模型: {request.model} → {m.name}")
                    return m
            if matches:
                logger.warning(f"[Router] 模糊匹配到 {request.model},但均不可用,回退自动选择")

    # ── 2. 候选(同模态 + 输入档案兼容) ──
    candidates = [m for m in registry.by_modality(request.task_type) if m.supports(request)]
    if not candidates:
        raise ModelUnavailableError(
            f"任务类型 {request.task_type.value} 没有与当前输入匹配的模型(图片数={len(request.images)})"
        )

    # ── 3. 可用性过滤 ──
    available: list[AIGCGenerationBase] = []
    for m in candidates:
        if await m.check_available():
            available.append(m)

    if not available:
        reasons = await _collect_unavailable_reasons(candidates)
        raise ModelUnavailableError(f"所有 {len(candidates)} 个候选模型均不可用:\n{reasons}")

    # ── 4. AI Agent 推荐(可选) ──
    agent_choice = await _try_agent_recommendation(request, available)
    if agent_choice is not None:
        return agent_choice

    # ── 5. 优先级 + 随机 ──
    available.sort(key=lambda m: m.priority, reverse=True)
    top_priority = available[0].priority
    top_group = [m for m in available if m.priority == top_priority]
    selected = random.choice(top_group)
    logger.info(f"[Router] 优先级选择: {selected.name} (priority={top_priority})")
    return selected


async def _try_agent_recommendation(
    request: GenerationRequest,
    candidates: list[AIGCGenerationBase],
) -> Optional[AIGCGenerationBase]:
    """尝试通过 AI Agent 推荐模型(钩子不存在或失败时静默降级)

    推荐入参把每个候选的 ModelCard 渲染为知识文本,LLM 依据"品类/优势/积分"
    完成选型;返回值按 name 映射回模型实例。
    """
    try:
        from gsuid_core.ai_core import gs_agent  # type: ignore[attr-defined]

        recommend_model: Any = getattr(gs_agent, "recommend_model", None)
        if recommend_model is None:
            return None

        result = await recommend_model(request, candidates)
        if result is None:
            return None
        # 钩子可能返回模型实例本身,或返回带 name 的对象/字符串
        if isinstance(result, AIGCGenerationBase):
            return result
        name = result if isinstance(result, str) else getattr(result, "name", "")
        for m in candidates:
            if m.name == name:
                return m
    except Exception as e:  # noqa: BLE001 — 外部钩子失败必须降级为确定性路由
        logger.debug(f"[Router] AI Agent 推荐失败: {e}")
    return None


__all__ = ["route"]
