"""
模型注册表模块
存放 MODEL_REGISTRY 和模型创建逻辑，解决循环导入问题
"""

import random
from typing import Dict, Tuple, Callable, Optional

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.ai_core.gs_agent import create_agent

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
            "千问Image2512模型，擅长中文提示词理解，适合各种风格的图像生成。优势：中文提示词理解能力强，支持复杂风格描述，输出质量稳定可靠，成本低。适用场景：中文场景的图像生成，简单需求，二次元动漫头像，通用图像生成。",
        ),
        (
            "qwen_2512_img2img",
            draw_img_by_img_by_qwen_2512,
            "image2image",
            "千问Qwen-Image2512 (图生图)",
            "千问Image2512模型，擅长中文提示词理解，适合各种风格的图像生成。优势：中文提示词理解能力强，支持复杂风格描述，输出质量稳定可靠。适用场景：中文场景的图像生成，需要精确描述的画面，艺术创作和插画。",
        ),
        (
            "qwen_2511",
            edit_img_by_qwen_edit_2511,
            "image_edit",
            "通义千问 Edit 2511",
            "专业的图像编辑模型。优势：中文指令理解准确，支持精确的区域编辑，可同时处理多图输入。适用场景：局部修图和编辑，多图融合处理，照片精修，添加或删除元素。",
        ),
        (
            "ace_step1.5",
            gen_music_by_ace_step_1_5,
            "music",
            "ACE Step 1.5",
            "音乐生成模型。优势：可以生成多种风格音乐，支持歌词输入，音乐质量较高。适用场景：背景音乐生成，创意音乐制作，配乐需求。",
        ),
        (
            "IndexTTS2",
            gen_speech_by_index_tts_2,
            "speech",
            "Index TTS 2",
            "语音合成模型。优势：语音自然，中文发音准确，支持多种语气。适用场景：文本转语音，有声内容制作，辅助阅读。",
        ),
        (
            "wan2.2_text2video",
            gen_video_by_text_by_wan2_2,
            "text2video",
            "Wan 2.2 Text2Video",
            "文生视频模型。优势：中文支持良好，可以生成分辨率较高的视频，动作流畅。适用场景：创意视频生成，动画制作，短视频创作。",
        ),
        (
            "wan2.2_img2video",
            gen_video_by_img_by_wan2_2,
            "image2video",
            "Wan 2.2 Image2Video",
            "图生视频模型。优势：可以让静态图片动起来，保持原图风格，中文支持良好。适用场景：让照片变生动，制作循环动画，基于图片的短视频。",
        ),
    ]

    for name, func, task_type, desc, knowledge in comfyui_models:
        registry[name] = ModelInfo(
            name=name,
            func=func,
            requirements=[ModelRequirement.COMFYUI_URL],
            task_type=task_type,
            description=desc,
            knowledge_content=knowledge,
        )

    # BLT 模型 - 需要 BLT API Key
    blt_models = [
        (
            "banana2",
            draw_image_by_banana2,
            "text2image",
            "Nano Bnana 2",
            "Gemini 3.1 Flash 图像生成模型，速度快，适合快速生成和预览。优势：生成速度非常快，支持快速迭代测试，质量稳定可控，适合批量生成。适用场景：需要较快速度但保持较好质量的图像，高清图像，精细画面。",
        ),
        (
            "banana_pro",
            draw_image_by_banana_pro,
            "text2image",
            "Nano Banana 1 Pro",
            "Nano Banana 2.2K 高质量图像生成模型。优势：图像质量非常高，细节丰富细腻，色彩表现优秀，适合专业输出。适用场景：需要最终输出的高质量图像，专业创作场景，商业项目，需要精细细节的画面。",
        ),
        (
            "banana2_edit",
            edit_img_by_banana2,
            "image_edit",
            "Nano Bnana 2 (编辑)",
            "快速图像编辑模型。优势：处理速度快，适合快速编辑。适用场景：较为复杂的图片修改。",
        ),
        (
            "banana_pro_edit",
            edit_img_by_banana_pro,
            "image_edit",
            "Nano Banana Pro (编辑)",
            "高质量图像编辑模型。优势：编辑质量高，细节处理好。适用场景：精细图片编辑，专业修图，需要高质量输出。",
        ),
    ]

    for name, func, task_type, desc, knowledge in blt_models:
        registry[name] = ModelInfo(
            name=name,
            func=func,
            requirements=[ModelRequirement.BLT_API],
            task_type=task_type,
            description=desc,
            knowledge_content=knowledge,
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


async def generate_model_selection_prompt(
    query: str,
    category: str,
) -> str:
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
    available_models = await availability_checker.filter_available(
        all_models,
        MODEL_REGISTRY,
    )

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


async def recommend_model(
    query: str,
    category: str,
    fallback: bool = True,
) -> Optional[str]:
    """
    为特定类别推荐模型（使用 Agent 智能选择）

    该函数会：
    1. 根据当前可用模型生成系统提示词
    2. 使用 Agent（需自行补充）根据提示词选择模型
    3. fallback 时按优先级选择

    Args:
        query: 用户需求描述
        category: 模型类别 (text2image, image2image, etc.)
        fallback: 当 Agent 选择失败时是否回退到按优先级选择

    Returns:
        推荐的模型名称，如果没有找到且fallback=False则返回None
    """

    try:
        # 1. 获取该类别所有模型
        all_models = [name for name, info in MODEL_REGISTRY.items() if info.task_type == category]

        if not all_models:
            logger.warning(f"[RHComfyUI][Agent] 类别 {category} 没有注册的模型")
            return None

        # 2. 过滤可用模型
        available_models = await availability_checker.filter_available(
            all_models,
            MODEL_REGISTRY,
        )

        if not available_models:
            logger.warning(f"[RHComfyUI][Agent] 类别 {category} 没有可用模型")
            return None

        # 3. 生成系统提示词
        system_prompt = await generate_model_selection_prompt(query, category)

        if not system_prompt:
            logger.warning("[RHComfyUI][Agent] 无法生成模型选择提示词")
            if fallback:
                return _get_priority_model(available_models, category) or (
                    random.choice(available_models) if available_models else None
                )
            return None

        # 4. 使用 Agent 选择模型（需自行补充 agent 部分）
        _agent = create_agent(system_prompt=system_prompt)
        selected_model = await _agent.run(query)

        # 5. 如果 Agent 选择成功，返回结果
        if selected_model and selected_model in available_models:
            logger.info(f"[RHComfyUI][Agent] 选择模型: {selected_model}")
            return selected_model

        # 6. fallback：按优先级选择
        if fallback:
            selected = _get_priority_model(available_models, category)
            if selected:
                logger.info(f"[RHComfyUI][Agent] Fallback 优先选择模型: {selected}")
                return selected
            # 如果没有优先级中的模型，随机选择
            selected = random.choice(available_models)
            logger.info(f"[RHComfyUI][Agent] Fallback 随机选择模型: {selected}")
            return selected

        return None

    except Exception as e:
        logger.error(f"[RHComfyUI][Agent] 推荐模型失败: {e}")

        # 出错时按优先级选择一个可用模型
        if fallback:
            all_models = [name for name, info in MODEL_REGISTRY.items() if info.task_type == category]
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
    category_models = [name for name, info in MODEL_REGISTRY.items() if info.task_type == category]

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

    # 如果提供了 query，尝试使用 Agent 智能推荐
    if query:
        try:
            rag_model = await recommend_model(query, category, fallback=False)
            if rag_model and rag_model in category_models:
                # 检查 Agent 推荐的模型是否可用
                result = await availability_checker.check_model(MODEL_REGISTRY[rag_model])
                if result.is_available:
                    logger.info(f"[RHComfyUI] Agent 推荐模型: {rag_model}")
                    return rag_model, MODEL_REGISTRY[rag_model].func
                else:
                    logger.warning(f"[RHComfyUI] Agent 推荐模型 {rag_model} 不可用，尝试其他模型")
        except Exception as e:
            logger.warning(f"[RHComfyUI] Agent 推荐失败: {e}")

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
