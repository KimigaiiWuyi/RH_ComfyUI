"""
模型注册表模块
存放 MODEL_REGISTRY 和模型创建逻辑，解决循环导入问题
"""

import random
from typing import Dict, Tuple, Callable, Optional

from gsuid_core.logger import logger
from gsuid_core.models import Event

from .constant import MODEL_PRIORITY
from .comfyui._request import (
    draw_img_by_qwen_2512,
    gen_music_by_ace_step_1_5,
    gen_speech_by_index_tts_2,
    edit_img_by_qwen_edit_2511,
    gen_video_by_img_by_wan2_2,
    gen_video_by_text_by_wan2_2,
    draw_img_by_img_by_qwen_2512,
)
from ..utils.blt.request import (
    edit_img_by_banana2,
    draw_image_by_banana2,
    edit_img_by_banana_pro,
    draw_image_by_banana_pro,
)
from .model_availability import (
    ModelInfo,
    ModelStatus,
    ModelRequirement,
    ModelUnavailableError,
    availability_checker,
)
from ..utils.database.models import RHBind
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

# 积分配置
Draw_Point: int = RHCOMFYUI_CONFIG.get_config("Draw_Point").data
Edit_Image_Point: int = RHCOMFYUI_CONFIG.get_config("Edit_Image_Point").data
Music_Point: int = RHCOMFYUI_CONFIG.get_config("Music_Point").data
Speech_Point: int = RHCOMFYUI_CONFIG.get_config("Speech_Point").data
Video_Point: int = RHCOMFYUI_CONFIG.get_config("Video_Point").data


def _create_model_registry() -> Dict[str, ModelInfo]:
    """创建模型注册表"""
    registry = {}

    # ComfyUI 模型 - 需要 ComfyUI 地址
    comfyui_models = [
        (
            "qwen_2512",
            draw_img_by_qwen_2512,
            "text2image",
            "千问Qwen-Image2512",
        ),
        (
            "qwen_2512_img2img",
            draw_img_by_img_by_qwen_2512,
            "image2image",
            "千问Qwen-Image2512 (图生图)",
        ),
        (
            "qwen_2511",
            edit_img_by_qwen_edit_2511,
            "image_edit",
            "通义千问 Edit 2511",
        ),
        (
            "ace_step1.5",
            gen_music_by_ace_step_1_5,
            "music",
            "ACE Step 1.5",
        ),
        (
            "IndexTTS2",
            gen_speech_by_index_tts_2,
            "speech",
            "Index TTS 2",
        ),
        (
            "wan2.2_text2video",
            gen_video_by_text_by_wan2_2,
            "text2video",
            "Wan 2.2 Text2Video",
        ),
        (
            "wan2.2_img2video",
            gen_video_by_img_by_wan2_2,
            "image2video",
            "Wan 2.2 Image2Video",
        ),
    ]

    for name, func, category, desc in comfyui_models:
        registry[name] = ModelInfo(
            name=name,
            func=func,
            requirements=[ModelRequirement.COMFYUI_URL],
            category=category,
            description=desc,
        )

    # BLT 模型 - 需要 BLT API Key
    blt_models = [
        (
            "banana2",
            draw_image_by_banana2,
            "text2image",
            "Nano Bnana 2",
        ),
        (
            "banana_pro",
            draw_image_by_banana_pro,
            "text2image",
            "Nano Banana 1 Pro",
        ),
        (
            "banana2_edit",
            edit_img_by_banana2,
            "image_edit",
            "Nano Bnana 2 (编辑)",
        ),
        (
            "banana_pro_edit",
            edit_img_by_banana_pro,
            "image_edit",
            "Nano Banana Pro (编辑)",
        ),
    ]

    for name, func, category, desc in blt_models:
        registry[name] = ModelInfo(
            name=name,
            func=func,
            requirements=[ModelRequirement.BLT_API],
            category=category,
            description=desc,
        )

    return registry


# 全局模型注册表
MODEL_REGISTRY: Dict[str, ModelInfo] = _create_model_registry()


# ===== 积分检查 =====
async def check_point(ev: Event, point: int) -> Tuple[bool, str]:
    """检查用户是否有足够的积分"""
    logger.info(f"[RHComfyUI] check_point: 用户:{ev.user_id} BotID:{ev.bot_id} 消费:{point}")

    bind = await RHBind.deduct_point(ev.user_id, ev.bot_id, point)
    now_point = await RHBind.get_point(ev.user_id, ev.bot_id)

    if bind:
        return True, f"💪 积分充足！已扣除{point}积分!\n📋 当前积分: {now_point}\n✅ 正在生成，预计将等待1分钟..."
    else:
        return False, f"❌ 积分不足！需要{point}积分！\n📋 当前积分: {now_point}"


def _get_priority_model(available_models: list, category: str) -> Optional[str]:
    """根据优先级从可用模型中选择"""
    priority_list = MODEL_PRIORITY.get(category, [])
    for model_name in priority_list:
        if model_name in available_models:
            return model_name
    return None


# ===== RAG 模型推荐 =====
async def recommend_model(
    query: str,
    category: str,
    limit: int = 3,
    fallback: bool = True,
) -> Optional[str]:
    """
    为特定类别推荐模型（带可用性预过滤）

    该函数会：
    1. 使用 RAG 获取候选模型
    2. 过滤掉当前不可用的模型
    3. 返回最佳匹配的可用模型
    4. fallback 时按优先级选择（默认优先 qwen）

    Args:
        query: 用户需求描述
        category: 模型类别 (text2image, image2image, etc.)
        limit: 查询结果数量
        fallback: 当没有找到匹配时是否回退到按优先级选择

    Returns:
        推荐的模型名称，如果没有找到且fallback=False则返回None
    """
    from gsuid_core.ai_core.rag import query_knowledge

    try:
        # 1. 使用 RAG 获取候选模型
        results = await query_knowledge(
            query=query,
            category=category,
            limit=limit,
        )

        # 2. 获取该类别所有模型
        all_models = [name for name, info in MODEL_REGISTRY.items() if info.category == category]

        # 3. 检查每个候选模型的可用性
        available_candidates = []
        for result in results:
            if result.payload is None:
                continue

            model_id = result.payload.get("id", "")
            model_name = model_id.split(":")[-1] if ":" in model_id else model_id

            # 检查可用性
            if model_name in MODEL_REGISTRY:
                check_result = await availability_checker.check_model(MODEL_REGISTRY[model_name])
                if check_result.is_available:
                    available_candidates.append((model_name, result.score))

        # 4. 如果有可用候选，返回最佳匹配
        if available_candidates:
            best_model = max(available_candidates, key=lambda x: x[1])
            logger.info(f"[RHComfyUI][RAG] 选择可用模型: {best_model[0]}")
            return best_model[0]

        # 5. fallback：按优先级选择（默认优先 qwen）
        if fallback and all_models:
            available_models = await availability_checker.filter_available(all_models, MODEL_REGISTRY)

            if available_models:
                # 按优先级选择，而不是随机选择
                selected = _get_priority_model(available_models, category)
                if selected:
                    logger.info(f"[RHComfyUI][RAG] Fallback 优先选择模型: {selected}")
                    return selected
                # 如果没有优先级中的模型，随机选择
                selected = random.choice(available_models)
                logger.info(f"[RHComfyUI][RAG] Fallback 随机选择模型: {selected}")
                return selected

        return None

    except Exception as e:
        logger.error(f"[RHComfyUI][RAG] 推荐模型失败: {e}")

        # 出错时按优先级选择一个可用模型
        if fallback:
            all_models = [name for name, info in MODEL_REGISTRY.items() if info.category == category]
            if all_models:
                available_models = await availability_checker.filter_available(all_models, MODEL_REGISTRY)
                if available_models:
                    selected = _get_priority_model(available_models, category)
                    if selected:
                        return selected
                    return random.choice(available_models)

        return None


# ===== 模型选择 =====
async def select_available_model(
    category: str,
    preferred_model: Optional[str] = None,
    query: Optional[str] = None,
) -> Tuple[str, Callable]:
    """
    选择一个可用的模型

    Args:
        category: 模型类别
        preferred_model: 优先选择的模型（如果可用）
        query: 用户提示词（可选），用于RAG智能推荐

    Returns:
        (模型名称, 模型函数)

    Raises:
        ModelUnavailableError: 如果该类别没有可用模型
    """
    # 获取该类别所有模型
    category_models = [name for name, info in MODEL_REGISTRY.items() if info.category == category]

    if not category_models:
        raise ModelUnavailableError(
            f"类别 {category} 没有注册的模型",
            "",
            ModelStatus.UNKNOWN,
        )

    # 如果指定了优先模型，先检查它
    if preferred_model and preferred_model in category_models:
        result = await availability_checker.check_model(MODEL_REGISTRY[preferred_model])
        if result.is_available:
            return preferred_model, MODEL_REGISTRY[preferred_model].func
        else:
            logger.warning(f"[RHComfyUI] 优先模型 {preferred_model} 不可用，尝试其他模型")

    # 如果提供了 query，尝试使用 RAG 智能推荐
    if query:
        try:
            rag_model = await recommend_model(query, category, limit=3, fallback=False)
            if rag_model and rag_model in category_models:
                # 检查 RAG 推荐的模型是否可用
                result = await availability_checker.check_model(MODEL_REGISTRY[rag_model])
                if result.is_available:
                    logger.info(f"[RHComfyUI] RAG 推荐模型: {rag_model}")
                    return rag_model, MODEL_REGISTRY[rag_model].func
                else:
                    logger.warning(f"[RHComfyUI] RAG 推荐模型 {rag_model} 不可用，尝试其他模型")
        except Exception as e:
            logger.warning(f"[RHComfyUI] RAG 推荐失败: {e}")

    # 检查该类别所有模型的可用性
    available_models = await availability_checker.filter_available(category_models, MODEL_REGISTRY)

    if not available_models:
        # 记录所有不可用的原因
        for name in category_models:
            result = await availability_checker.check_model(MODEL_REGISTRY[name])
            logger.warning(f"[RHComfyUI] 模型 {name} 不可用: {result.reason}")

        raise ModelUnavailableError(f"类别 {category} 没有可用模型，请检查配置", "", ModelStatus.UNKNOWN)

    # 随机选择一个可用模型
    selected = random.choice(available_models)
    logger.info(f"[RHComfyUI] 从类别 {category} 选择模型: {selected}")
    return selected, MODEL_REGISTRY[selected].func
