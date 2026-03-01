import random
from typing import Tuple, Optional, Annotated

from PIL import Image
from msgspec import Meta

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.resource_manager import RM

from .model_wrapper import recommend_model
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
    "qwen_2512_with_lora": draw_img_by_img_by_qwen_2512,
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
) -> Image.Image:
    """
    调用AI模型进行文生图的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：qwen_2512, banana2, banana_pro
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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
) -> Image.Image:
    """
    调用AI模型进行图生图的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：qwen_2512_with_lora
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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
):
    """
    调用AI模型进行图片编辑的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：qwen_2511, banana2, banana_pro
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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
):
    """
    调用AI模型进行音乐生成的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：ace_step1.5
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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
):
    """
    调用AI模型进行语音生成的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：IndexTTS2
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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
):
    """
    调用AI模型进行文生视频的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：wan2.2_text2video
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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
):
    """
    调用AI模型进行图生视频的工具

    当model参数为空时，会根据用户需求自动查询向量库选择合适的模型。
    可用模型：wan2.2_img2video
    """
    if model is None:
        # 使用RAG查询推荐合适的模型
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

    model_func = image2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    image = await RM.get(image_id)
    result = await model_func(prompt, image, w, h)
    if result is not None:
        return MessageSegment.video(result)
    return result
