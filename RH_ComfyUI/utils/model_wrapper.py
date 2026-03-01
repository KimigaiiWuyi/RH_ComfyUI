"""模型知识库包装 - 使用框架的 query_knowledge 方法"""

import random
from typing import Optional

from gsuid_core.ai_core.rag import query_knowledge
from gsuid_core.ai_core.register import ai_entity

from .model_knowledge import MODEL_KNOWLEDGE, get_model_names_by_category


def register_model_kai():
    """注册模型知识到框架"""
    plugin_name = "RH_ComfyUI"
    knowledge_type = "model"

    for category in MODEL_KNOWLEDGE:
        for model_key in MODEL_KNOWLEDGE[category]:
            info = MODEL_KNOWLEDGE[category][model_key]

            knowledge_base = {
                "id": f"{plugin_name}:{knowledge_type}:{category}:{model_key}",
                "plugin": plugin_name,
                "type": knowledge_type,
                "category": category,
                "title": info["title"],
                "content": info["content"],
                "tags": info["tags"],
            }

            # 添加 _hash 字段，构成完整的 KnowledgePoint
            knowledge_point = {
                **knowledge_base,
                "_hash": "",  # 会被 rag.py 中的 calculate_knowledge_hash 覆盖
            }

            ai_entity(knowledge_point)  # type: ignore


async def recommend_model(
    query: str,
    category: str,
    limit: int = 3,
    fallback: bool = True,
) -> Optional[str]:
    """为特定类别推荐模型

    Args:
        query: 用户需求描述
        category: 模型类别 (text2image, image2image, etc.)
        limit: 查询结果数量
        fallback: 当没有找到匹配时是否回退到随机选择

    Returns:
        推荐的模型名称，如果没有找到且fallback=False则返回None
    """
    try:
        # 使用框架的 query_knowledge 方法
        results = await query_knowledge(
            query=query,
            category=category,
            limit=limit,
        )

        if not results or len(results) == 0:
            if fallback:
                models = get_model_names_by_category(category)
                if models:
                    return random.choice(models)
            return None

        # 选择分数最高的模型
        best_point = sorted(results, key=lambda x: x.score, reverse=True)[0]

        # 从id中提取模型名称: RH_ComfyUI:model:text2image:qwen_2512 -> qwen_2512
        if best_point.payload is None:
            return None

        model_id = best_point.payload.get("id", "")
        if ":" in model_id:
            model_name = model_id.split(":")[-1]
        else:
            model_name = model_id

        return model_name
    except Exception:
        if fallback:
            models = get_model_names_by_category(category)
            if models:
                return random.choice(models)
        return None


register_model_kai()
