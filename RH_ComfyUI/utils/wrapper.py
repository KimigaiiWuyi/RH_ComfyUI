"""主包装器模块"""

from typing import Tuple, Callable, Optional

from gsuid_core.logger import logger
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.resource_manager import RM

from .models import (
    MODEL_REGISTRY,
    ModelStatus,
    ModelUnavailableError,
    recommend_model,
    availability_checker,
)
from .points import (
    Draw_Point,
    Music_Point,
    Video_Point,
    Speech_Point,
    Edit_Image_Point,
    check_point,
)


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
        query: 用户提示词（可选），用于Agent智能推荐

    Returns:
        (模型名称, 模型函数)

    Raises:
        ModelUnavailableError: 如果该类别没有可用模型
    """
    import random

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
            rag_model = await recommend_model(query, category)
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


# ===== AI 工具函数 =====
@ai_tools(check_func=check_point, point=Draw_Point)
async def gen_image_by_text(
    prompt: str,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    """
    文生图工具
    根据文字描述生成图片

    根据用户提供的文字描述，从零开始创建生成图片。
    适用于创意设计、插画、海报、概念图等视觉内容生成场景。

    Args:
        prompt: 要生成图片的文字描述内容，支持详细描述
        w: 生成图片的宽度，默认720
        h: 生成图片的高度，默认1280
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的图片结果对象

    Example:
        >>> await gen_image_by_text("一只可爱的猫咪在草地上玩耍")
        >>> await gen_image_by_text("未来城市概念图", w=1920, h=1080)
    """
    model_name, model_func = await select_available_model(
        "text2image",
        model,
        query=prompt,
    )
    result = await model_func(prompt, w, h)
    return result


@ai_tools(check_func=check_point, point=Draw_Point)
async def gen_image_by_img(
    prompt: str,
    image_id: str,
    model: Optional[str] = None,
):
    """
    图生图工具
    以现有图片为基础，根据文字描述生成新图片

    以现有图片为基础，根据文字描述对原图进行重新创作和生成，保留原图的大部分内容或风格特征。
    适用于基于已有图片进行重新绘图的场景。

    Args:
        prompt: 要生成图片的文字描述内容，描述希望在原图基础上生成的新图片特征
        image_id: 原始图片的资源ID，用于获取基础图片
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的图片结果对象

    Example:
        >>> await gen_image_by_img("一只穿着西装的猫", image_id="cat_001")
        >>> await gen_image_by_img("印象派风格的风景画", image_id="landscape_002")
    """
    model_name, model_func = await select_available_model(
        "image2image",
        model,
        query=prompt,
    )
    image = await RM.get(image_id)
    result = await model_func(prompt, image)
    return result


@ai_tools(check_func=check_point, point=Edit_Image_Point)
async def edit_image(
    prompt: str,
    image_id: str,
    model: Optional[str] = None,
):
    """
    图片编辑工具

    对现有图片进行编辑和修改
    对已有图片进行编辑，包括局部修改、元素添加或删除、风格转换等操作。

    Args:
        prompt: 编辑指令，描述要进行的修改
        image_id: 要编辑的图片资源ID
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        编辑后的图片结果对象

    Example:
        >>> await edit_image("给图片加上文字：Hello", image_id="photo_001")
        >>> await edit_image("删除背景中的路人", image_id="street_001")
    """
    model_name, model_func = await select_available_model(
        "image_edit",
        model,
        query=prompt,
    )
    image = await RM.get(image_id)
    result = await model_func(prompt, image)
    return result


async def gen_edit_img_by_img(
    prompt: str,
    image_id_list: list,
) -> str:
    """
    编辑图片（多图）

    对已有图片进行编辑，支持多图输入。

    Args:
        prompt: 编辑指令，描述要进行的修改
        image_id_list: 要编辑的图片资源ID列表

    Returns:
        编辑后的图片结果
    """
    images = [await RM.get(img_id) for img_id in image_id_list]
    model_name, model_func = await select_available_model(
        "image_edit",
        query=prompt,
    )
    result = await model_func(prompt, images)
    return result


@ai_tools(check_func=check_point, point=Music_Point)
async def gen_music(
    prompt: str,
    model: Optional[str] = None,
):
    """
    音乐生成工具
    根据描述生成音乐

    根据文字描述生成音乐，可以指定风格、情绪、用途等特征。

    Args:
        prompt: 音乐描述，描述想要的音乐风格、情绪、用途等
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的音频结果对象

    Example:
        >>> await gen_music("轻松愉快的背景音乐，适合咖啡厅")
        >>> await gen_music("动感电子音乐，适合运动视频")
    """
    model_name, model_func = await select_available_model(
        "music",
        model,
        query=prompt,
    )
    result = await model_func(prompt)
    return result


@ai_tools(check_func=check_point, point=Speech_Point)
async def gen_speech(
    text: str,
    model: Optional[str] = None,
):
    """
    语音生成工具

    将输入的文字内容转换为自然语音输出。

    Args:
        text: 要转换的文字内容
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的语音结果对象

    Example:
        >>> await gen_speech("欢迎使用 RH_ComfyUI，这是一个强大的 AI 工具。")
        >>> await gen_speech("今天天气真好，我们出去走走吧。")
    """
    model_name, model_func = await select_available_model(
        "speech",
        model,
        query=text,
    )
    result = await model_func(text)
    return result


@ai_tools(check_func=check_point, point=Video_Point)
async def gen_video_by_text(
    prompt: str,
    duration: int = 5,
    model: Optional[str] = None,
):
    """
    文生视频工具

    根据文字描述直接生成视频内容。

    Args:
        prompt: 视频内容描述，描述想要的视频场景、动作、氛围等
        duration: 视频时长（秒），默认5秒
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的视频结果对象

    Example:
        >>> await gen_video_by_text("一只猫在草地上追蝴蝶", duration=5)
        >>> await gen_video_by_text("日出时分海边风景", duration=10)
    """
    model_name, model_func = await select_available_model(
        "text2video",
        model,
        query=prompt,
    )
    result = await model_func(prompt, duration)
    return result


@ai_tools(check_func=check_point, point=Video_Point)
async def gen_video_by_img(
    prompt: str,
    image_id: str,
    duration: int = 5,
    model: Optional[str] = None,
):
    """
    图生视频工具

    将静态图片转换为动态视频，可以添加运动效果。

    Args:
        prompt: 视频描述，描述想要的运动效果和变化
        image_id: 源图片的资源ID
        duration: 视频时长（秒），默认5秒
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的视频结果对象

    Example:
        >>> await gen_video_by_img("云朵飘动，水波荡漾", image_id="landscape_001", duration=5)
        >>> await gen_video_by_img("人物转身离开", image_id="person_001", duration=3)
    """
    model_name, model_func = await select_available_model(
        "image2video",
        model,
        query=prompt,
    )
    image = await RM.get(image_id)
    result = await model_func(prompt, image, duration)
    return result
