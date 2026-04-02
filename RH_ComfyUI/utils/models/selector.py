"""模型选择器 - 使用 Agent 智能选择模型"""

from typing import Optional

from gsuid_core.logger import logger
from gsuid_core.ai_core.gs_agent import create_agent

from .priority import _get_priority_model
from .registry import MODEL_REGISTRY
from .availability import availability_checker


async def generate_model_selection_prompt(query: str, category: str) -> str:
    """
    为模型选择生成系统提示词

    根据当前可用模型动态生成提示词，供 Agent 使用来选择最合适的模型。

    Args:
        query: 用户需求描述
        category: 模型类别 (text2image, image2image, etc.)

    Returns:
        生成的系统提示词
    """
    # 获取该类别所有模型
    all_models = [name for name, info in MODEL_REGISTRY.items() if info.task_type == category]

    # 过滤可用模型
    available_models = await availability_checker.filter_available(all_models, MODEL_REGISTRY)

    if not available_models:
        return ""

    # 构建模型列表描述
    model_list = []
    for name in available_models:
        info = MODEL_REGISTRY[name]
        model_list.append(f"- **{name}**: {info.description}\n  详细说明：{info.knowledge_content}")

    category_names = {
        "text2image": "文生图",
        "image2image": "图生图",
        "image_edit": "图片编辑",
        "text2video": "文生视频",
        "image2video": "图生视频",
        "music": "音乐生成",
        "speech": "语音生成",
    }
    category_name = category_names.get(category, category)

    prompt = f"""你是一个专业的 AI 模型选择助手。

## 任务
根据用户的需求描述，从以下可用模型中选择最合适的一个。

## 用户需求
{query}

## 可用模型列表（{category_name}类别）
{chr(10).join(model_list)}

## 选择规则
1. 仔细分析用户需求的类型、风格、质量要求
2. 考虑各模型的特点和优势，选择最匹配的模型
3. 优先选择能够最好满足用户需求的模型
4. 只返回一个模型名称作为最终选择，不要返回其他内容

## 输出格式
只需返回模型名称（必须是上述列表中的一个），不需要任何解释或额外文字。
"""
    return prompt


async def recommend_model(query: str, category: str) -> Optional[str]:
    """
    为特定类别推荐模型（使用 Agent 智能选择）

    该函数会：
    1. 根据当前可用模型生成系统提示词
    2. 使用 Agent 根据提示词选择模型

    Args:
        query: 用户需求描述
        category: 模型类别 (text2image, image2image, etc.)

    Returns:
        推荐的模型名称，如果选择失败则返回None
    """
    # 1. 获取该类别所有模型
    all_models = [name for name, info in MODEL_REGISTRY.items() if info.task_type == category]

    if not all_models:
        logger.warning(f"[RHComfyUI][Agent] 类别 {category} 没有注册的模型")
        return None

    # 2. 过滤可用模型
    available_models = await availability_checker.filter_available(all_models, MODEL_REGISTRY)

    if not available_models:
        logger.warning(f"[RHComfyUI][Agent] 类别 {category} 没有可用模型")
        return None

    # 3. 生成系统提示词
    system_prompt = await generate_model_selection_prompt(query, category)

    if not system_prompt:
        logger.warning("[RHComfyUI][Agent] 无法生成模型选择提示词")
        return None

    # 4. 使用 Agent 选择模型
    _agent = create_agent(system_prompt=system_prompt)
    selected_model = await _agent.run(query)

    # 5. 验证选择结果
    if selected_model and selected_model in available_models:
        logger.info(f"[RHComfyUI][Agent] 选择模型: {selected_model}")
        return selected_model

    # 6. Agent 选择失败，按优先级兜底
    logger.warning(f"[RHComfyUI][Agent] Agent 选择结果无效: {selected_model}，使用优先级兜底")
    return _get_priority_model(available_models, category)
