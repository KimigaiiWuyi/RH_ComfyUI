import random
from typing import Tuple, Optional, Annotated

from PIL import Image
from msgspec import Meta

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.resource_manager import RM

from .model_wrapper import (
    recommend_model,
)
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
from ..utils.database.models import RHBind
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

Draw_Point: int = RHCOMFYUI_CONFIG.get_config("Draw_Point").data
Edit_Image_Point: int = RHCOMFYUI_CONFIG.get_config("Edit_Image_Point").data
Music_Point: int = RHCOMFYUI_CONFIG.get_config("Music_Point").data
Speech_Point: int = RHCOMFYUI_CONFIG.get_config("Speech_Point").data
Video_Point: int = RHCOMFYUI_CONFIG.get_config("Video_Point").data

text2image_workflow = {
    "qwen_2512": draw_img_by_qwen_2512,
    "banana2": draw_image_by_banana2,
    "banana_pro": draw_image_by_banana_pro,
}

image2image_workflow = {
    "qwen_2512_img2img": draw_img_by_img_by_qwen_2512,
}

image_edit_workflow = {
    "qwen_2511": edit_img_by_qwen_edit_2511,
    "banana2": edit_img_by_banana2,
    "banana_pro": edit_img_by_banana_pro,
}

music_workflow = {
    "ace_step1.5": gen_music_by_ace_step_1_5,
}

speech_workflow = {
    "IndexTTS2": gen_speech_by_index_tts_2,
}

text2video_workflow = {
    "wan2.2_text2video": gen_video_by_text_by_wan2_2,
}

image2video_workflow = {
    "wan2.2_img2video": gen_video_by_img_by_wan2_2,
}


async def check_point(ev: Event, point: int) -> Tuple[bool, str]:
    """
    检查用户是否有足够的积分
    """
    logger.info(f"[RHComfyUI] check_point: 用户:{ev.user_id} BotID:{ev.bot_id} 消费:{point}")

    bind = await RHBind.deduct_point(
        ev.user_id,
        ev.bot_id,
        point,
    )

    if bind:
        return True, f"💪 积分充足！已扣除{point}积分!\n✅ 正在生成，预计将等待1分钟..."
    else:
        return False, f"❌ 积分不足！需要{point}积分！"


@ai_tools(check_func=check_point, point=Draw_Point)
async def gen_image_by_text(
    prompt: Annotated[str, Meta(description="生成图片的提示词")],
    w: Annotated[int, Meta(description="生成图片的宽度")] = 720,
    h: Annotated[int, Meta(description="生成图片的高度")] = 1280,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
) -> Image.Image:
    """
    文生图工具：根据文字描述生成图片

    适用于：
    - 需要从零开始创建图片的场景
    - 创意设计、插画、海报等视觉内容生成
    - 根据文字描述生成概念图或示意图
    - 各种艺术风格的图像创作（写实、动漫、油画等）

    可用模型：
    - qwen_2512: 通义千问高质量文生图模型，支持多种风格
    - banana2: 高效轻量级文生图模型
    - banana_pro: 高质量文生图专业模型

    根据用户对质量、速度、成本的要求选择合适的模型
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"生成图片: {prompt}",
                category="text2image",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(text2image_workflow.keys()))

    if model is None:
        model = random.choice(list(text2image_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = text2image_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, w, h)
    return result


@ai_tools(check_func=check_point, point=Draw_Point)
async def gen_image_by_img(
    prompt: Annotated[str, Meta(description="生成图片的提示词")],
    image_id: Annotated[str, Meta(description="生成图片的基础图片ID")],
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
) -> Image.Image:
    """
    图生图工具：以现有图片为基础，根据文字描述生成新图片

    适用于：
    - 基于已有图片进行重新绘图, 保留原图大部分内容生成全新的图片

    可用模型：
    - qwen_2512_img2img: 通义千问图生图模型，支持高质量风格转换
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"图生图: {prompt}",
                category="image2image",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(image2image_workflow.keys()))

    if model is None:
        model = random.choice(list(image2image_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = image2image_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")

    image = await RM.get(image_id)
    result = await model_func(prompt, image)
    return result


@ai_tools(check_func=check_point, point=Edit_Image_Point)
async def gen_edit_img_by_img(
    prompt: Annotated[str, Meta(description="编辑图片的提示词")],
    image_id_list: Annotated[list[str], Meta(description="编辑图片的基础图片ID列表")],
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
):
    """
    图片编辑工具：对已有图片进行智能编辑和修改

    适用于：
    - 图片内容的智能替换或修改（如换装、换背景）
    - 局部区域编辑和修复
    - 多图片融合或创意编辑
    - 图像中物体的增删改
    - 给图片添加文字

    可用模型：
    - qwen_2511: 通义千问图片编辑模型
    - banana2: 高效轻量级图片编辑模型
    - banana_pro: 高质量图片编辑专业模型

    根据用户对质量、速度、成本的要求选择合适的模型
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"编辑图片: {prompt}",
                category="image_edit",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(image_edit_workflow.keys()))

    if model is None:
        model = random.choice(list(image_edit_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = image_edit_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")

    image_list = [await RM.get(image_id) for image_id in image_id_list]
    result = await model_func(prompt, image_list)
    return result


@ai_tools(check_func=check_point, point=Music_Point)
async def gen_music(
    style_prompt: Annotated[str, Meta(description="生成音乐的风格提示词")],
    lyric_prompt: Annotated[Optional[str], Meta(description="生成音乐的歌词提示词")] = None,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
):
    """
    音乐生成工具：根据风格和歌词描述生成音乐

    适用于：
    - 创作背景音乐、配乐等纯音乐
    - 根据歌词生成带人声的歌曲
    - 各种音乐风格的创作（流行、古典、电子、摇滚等）
    - 游戏配乐、视频配乐等场景音乐生成

    可用模型：
    - ace_step1.5: 高质量音乐生成模型
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"生成音乐: {style_prompt} {'' if lyric_prompt is None else lyric_prompt}",
                category="music",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(music_workflow.keys()))

    if model is None:
        model = random.choice(list(music_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = music_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(style_prompt, lyric_prompt)
    if result is not None:
        return MessageSegment.record(result)
    return result


@ai_tools(check_func=check_point, point=Speech_Point)
async def gen_speech(
    text: Annotated[str, Meta(description="生成语音的文本")],
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
):
    """
    语音生成工具：将文字转换为语音音频

    适用于：
    - 文字朗读和有声书制作
    - 视频配音和旁白生成
    - 语音播报和通知音效
    - 虚拟主播和数字人配音

    可用模型：
    - IndexTTS2: 高质量语音合成模型
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"生成语音: {text}",
                category="speech",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(speech_workflow.keys()))

    if model is None:
        model = random.choice(list(speech_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = speech_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(text)
    if result is not None:
        return MessageSegment.record(result)
    return result


@ai_tools(check_func=check_point, point=Video_Point)
async def gen_video_by_text(
    prompt: Annotated[str, Meta(description="生成视频的提示词")],
    w: Annotated[int, Meta(description="生成视频的宽度")] = 720,
    h: Annotated[int, Meta(description="生成视频的高度")] = 1280,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
):
    """
    文生视频工具：根据文字描述生成视频

    适用于：
    - 从零开始创作动画或短视频
    - 概念视频和动态视觉内容生成
    - 故事板或脚本的可视化
    - 各类动态场景的创意展示

    可用模型：
    - wan2.2_text2video: 高质量文生视频模型
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"生成视频: {prompt}",
                category="text2video",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(text2video_workflow.keys()))

    if model is None:
        model = random.choice(list(text2video_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = text2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, w, h)
    if result is not None:
        return MessageSegment.video(result)
    return result


@ai_tools(check_func=check_point, point=Video_Point)
async def gen_video_by_img(
    prompt: Annotated[str, Meta(description="生成视频的提示词")],
    image_id: Annotated[str, Meta(description="生成视频的基础图片ID")],
    w: Annotated[int, Meta(description="生成视频的宽度")] = 720,
    h: Annotated[int, Meta(description="生成视频的高度")] = 1280,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时AI会根据需求自动选择")] = None,
    model_knowledge: Annotated[Optional[str], Meta(description="模型知识参考，由AI根据用户需求预先检索")] = None,
):
    """
    图生视频工具：以图片为基础生成动态视频

    适用于：
    - 将静态图片转化为动态视频
    - 图片中的静态元素添加动态效果
    - 照片活化或视频化
    - 视觉素材的动态展示和演绎

    可用模型：
    - wan2.2_img2video: 高质量图生视频模型
    """
    if model is None:
        # 向后兼容：如果AI没有选择模型，使用原有的RAG方式
        try:
            model = await recommend_model(
                query=f"图生视频: {prompt}",
                category="image2video",
                fallback=True,
            )
        except Exception as e:
            logger.warning(f"[RHComfyUI][RAG] 模型推荐失败，使用随机选择: {e}")
            model = random.choice(list(image2video_workflow.keys()))

    if model is None:
        model = random.choice(list(image2video_workflow.keys()))

    if model_knowledge:
        logger.info(f"[RHComfyUI] 使用AI选择的模型: {model} (基于知识检索)")

    model_func = image2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    image = await RM.get(image_id)
    result = await model_func(prompt, image, w, h)
    if result is not None:
        return MessageSegment.video(result)
    return result
