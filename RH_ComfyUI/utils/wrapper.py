from typing import List, Optional

from gsuid_core.segment import MessageSegment
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.resource_manager import RM

# 导入 model_wrapper 以注册模型知识库到 RAG
from . import model_wrapper  # noqa: F401
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

# 工作流字典（保持与原代码兼容）
text2image_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "text2image"}

image2image_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "image2image"}

image_edit_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "image_edit"}

music_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "music"}

speech_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "speech"}

text2video_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "text2video"}

image2video_workflow = {name: info.func for name, info in MODEL_REGISTRY.items() if info.category == "image2video"}


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

    适用于：
    - 需要从零开始创建图片的场景
    - 创意设计、插画、海报等视觉内容生成
    - 根据文字描述生成概念图或示意图

    可用模型：
    - qwen_2512: 通义千问高质量文生图模型（需要配置 ComfyUI 地址）
    - banana2: 高效轻量级文生图模型（需要配置 BLT API Key）
    - banana_pro: 高质量文生图专业模型（需要配置 BLT API Key）
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

    适用于：
    - 基于已有图片进行重新绘图，保留原图大部分内容生成全新的图片

    可用模型：
    - qwen_2512_img2img: 通义千问图生图模型（需要配置 ComfyUI 地址）
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

    适用于：
    - 图片内容的智能替换或修改（如换装、换背景）
    - 局部区域编辑和修复
    - 多图片融合或创意编辑

    可用模型：
    - qwen_2511: 通义千问图片编辑模型（需要配置 ComfyUI 地址）
    - banana2: 高效轻量级图片编辑模型（需要配置 BLT API Key）
    - banana_pro: 高质量图片编辑专业模型（需要配置 BLT API Key）
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
):
    """
    音乐生成工具：根据风格和歌词描述生成音乐

    适用于：
    - 创作背景音乐、配乐等纯音乐
    - 根据歌词生成带人声的歌曲

    可用模型：
    - ace_step1.5: 高质量音乐生成模型（需要配置 ComfyUI 地址）
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

    适用于：
    - 文字朗读和有声书制作
    - 视频配音和旁白生成

    可用模型：
    - IndexTTS2: 高质量语音合成模型（需要配置 ComfyUI 地址）
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

    适用于：
    - 从零开始创作动画或短视频
    - 概念视频和动态视觉内容生成

    可用模型：
    - wan2.2_text2video: 高质量文生视频模型（需要配置 ComfyUI 地址）
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

    适用于：
    - 将静态图片转化为动态视频
    - 图片中的静态元素添加动态效果

    可用模型：
    - wan2.2_img2video: 高质量图生视频模型（需要配置 ComfyUI 地址）
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
