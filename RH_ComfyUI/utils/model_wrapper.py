"""
模型知识库包装
提供 RAG 预过滤和可用性检查集成
"""

from gsuid_core.ai_core.register import ai_entity

from .model_knowledge import MODEL_KNOWLEDGE

PLUGIN_NAME = "RH_ComfyUI"


def register_model_kai():
    """注册模型知识到框架"""
    knowledge_type = "model"

    for category in MODEL_KNOWLEDGE:
        for model_key in MODEL_KNOWLEDGE[category]:
            info = MODEL_KNOWLEDGE[category][model_key]

            knowledge_base = {
                "id": f"{PLUGIN_NAME}:{knowledge_type}:{category}:{model_key}",
                "plugin": PLUGIN_NAME,
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


register_model_kai()
