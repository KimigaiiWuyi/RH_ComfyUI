from typing import List, Optional

from gsuid_core.segment import Message, MessageSegment
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.resource_manager import RM

from .model_registry import (
    MODEL_REGISTRY,
    Draw_Point,
    Music_Point,
    Video_Point,
    Speech_Point,
    Edit_Image_Point,
    check_point,
    select_available_model,
)

# 工作流字典
text2image_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "text2image"}

image2image_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "image2image"}

image_edit_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "image_edit"}

music_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "music"}

speech_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "speech"}

text2video_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "text2video"}

image2video_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.task_type == "image2video"}


# ===== AI 工具函数 =====
@ai_tools(check_func=check_point, point=Draw_Point)
async def gen_image_by_text(
    prompt: str,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    """
    文生图工具：根据文字描述生成图片

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
    图生图工具：以现有图片为基础，根据文字描述生成新图片

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
async def gen_edit_img_by_img(
    prompt: str,
    image_id_list: List[str],
    model: Optional[str] = None,
):
    """
    图片编辑工具：对已有图片进行智能编辑和修改

    对已有图片进行智能编辑、修改或替换，支持局部区域编辑和修复、多图片融合等操作。
    适用于图片内容替换、换背景、局部修改、图片融合等场景。

    Args:
        prompt: 图片编辑的具体要求描述，如"将背景替换为海边"或"添加眼镜"
        image_id_list: 要编辑的图片资源ID列表，支持多图片输入
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        编辑后的图片结果对象

    Example:
        >>> await gen_edit_img_by_img("换上一件红色外套", image_id_list=["person_001"])
        >>> await gen_edit_img_by_img("背景替换为城市夜景", image_id_list=["photo_002"])
    """
    model_name, model_func = await select_available_model(
        "image_edit",
        model,
        query=prompt,
    )
    image_list = [await RM.get(image_id) for image_id in image_id_list]
    result = await model_func(prompt, image_list)
    return result


@ai_tools(check_func=check_point, point=Music_Point)
async def gen_music(
    style_prompt: str,
    lyric_prompt: Optional[str] = None,
    model: Optional[str] = None,
) -> Message:
    """
    音乐生成工具：根据风格和歌词描述生成音乐

    根据用户提供的风格描述和可选歌词内容，自动生成对应的音乐作品。
    适用于创作背景音乐、配乐、歌曲等音乐生成场景。

    Args:
        style_prompt: 音乐风格描述，如"欢快的流行音乐"或"悲伤的钢琴曲"
        lyric_prompt: 可选，歌词内容，用于生成带人声的歌曲，None则生成纯音乐
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的音频消息对象，包含音乐文件

    Example:
        >>> await gen_music("欢快的电子音乐")
        >>> await gen_music("浪漫的钢琴曲", lyric_prompt="月光下我们起舞")
    """
    model_name, model_func = await select_available_model(
        "music",
        model,
        query=style_prompt,
    )
    result = await model_func(style_prompt, lyric_prompt)
    if result is not None:
        return MessageSegment.record(result)
    return result


@ai_tools(check_func=check_point, point=Speech_Point)
async def gen_speech(
    text: str,
    model: Optional[str] = None,
):
    """
    语音生成工具：将文字转换为语音音频

    将用户提供的文字内容通过语音合成技术转换为自然流畅的语音音频。
    适用于文字朗读、有声书制作、视频配音、旁白生成等场景。

    Args:
        text: 要转换为语音的文字内容，支持较长文本
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的音频消息对象，包含语音文件

    Example:
        >>> await gen_speech("欢迎收听今天的新闻播报")
        >>> await gen_speech("这是一段视频旁白内容")
    """
    model_name, model_func = await select_available_model(
        "speech",
        model,
        query=text,
    )
    result = await model_func(text)
    if result is not None:
        return MessageSegment.record(result)
    return result


@ai_tools(check_func=check_point, point=Video_Point)
async def gen_video_by_text(
    prompt: str,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    """
    文生视频工具：根据文字描述生成视频

    根据用户提供的文字描述，从零开始创作生成动态视频内容。
    适用于动画创作、短视频生成、概念视频制作等场景。

    Args:
        prompt: 要生成视频的文字描述内容，支持描述场景、动作、氛围等
        w: 生成视频的宽度，默认720
        h: 生成视频的高度，默认1280
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的视频消息对象

    Example:
        >>> await gen_video_by_text("一只狗在海边奔跑，夕阳西下")
        >>> await gen_video_by_text("城市街道下雨场景", w=1920, h=1080)
    """
    model_name, model_func = await select_available_model(
        "text2video",
        model,
        query=prompt,
    )
    result = await model_func(prompt, w, h)
    if result is not None:
        return MessageSegment.video(result)
    return result


@ai_tools(check_func=check_point, point=Video_Point)
async def gen_video_by_img(
    prompt: str,
    image_id: str,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    """
    图生视频工具：以图片为基础生成动态视频

    以静态图片为基础，根据文字描述为图片中的元素添加动态效果，生成动态视频。
    适用于将静态图片转化为动态视频、图片元素动效添加等场景。

    Args:
        prompt: 描述视频中动态效果的文字内容
        image_id: 基础图片的资源ID，用于获取原始静态图片
        w: 生成视频的宽度，默认720
        h: 生成视频的高度，默认1280
        model: 可选，指定使用的模型名称，默认为自动选择可用模型

    Returns:
        生成的视频消息对象

    Example:
        >>> await gen_video_by_img("风吹动树叶", image_id="static_001")
        >>> await gen_video_by_img("云朵缓缓飘动", image_id="landscape_002")
    """
    model_name, model_func = await select_available_model(
        "image2video",
        model,
        query=prompt,
    )
    image = await RM.get(image_id)
    result = await model_func(prompt, image, w, h)
    if result is not None:
        return MessageSegment.video(result)
    return result
