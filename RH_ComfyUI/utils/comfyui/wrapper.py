import random
from typing import Optional, Annotated

from PIL import Image
from msgspec import Meta

from gsuid_core.ai_core.register import ai_tools
from gsuid_core.utils.resource_manager import RM

from ._request import (
    draw_img_by_qwen_2512,
    gen_music_by_ace_step_1_5,
    gen_speech_by_index_tts_2,
    edit_img_by_qwen_edit_2511,
    gen_video_by_img_by_wan2_2,
    gen_video_by_text_by_wan2_2,
    draw_img_by_img_by_qwen_2512,
)

text2image_workflow = {
    "qwen_2512": draw_img_by_qwen_2512,
}

image2image_workflow = {
    "qwen_2512_with_lora": draw_img_by_img_by_qwen_2512,
}

image_edit_workflow = {
    "qwen_2511": edit_img_by_qwen_edit_2511,
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


@ai_tools
async def gen_image_by_text(
    prompt: Annotated[str, Meta(description="生成图片的提示词")],
    w: Annotated[int, Meta(description="生成图片的宽度")] = 720,
    h: Annotated[int, Meta(description="生成图片的高度")] = 1280,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
) -> Image.Image:
    """
    调用AI模型进行文生图的工具
    """
    if model is None:
        model = random.choice(list(text2image_workflow.keys()))

    model_func = text2image_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, w, h)
    return result


@ai_tools
async def gen_image_by_img(
    prompt: Annotated[str, Meta(description="生成图片的提示词")],
    image_id: Annotated[str, Meta(description="生成图片的基础图片ID")],
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
) -> Image.Image:
    """
    调用AI模型进行图生图的工具
    """
    if model is None:
        model = random.choice(list(image2image_workflow.keys()))

    model_func = image2image_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")

    image = await RM.get(image_id)
    result = await model_func(prompt, image)
    return result


@ai_tools
async def gen_edit_img_by_img(
    prompt: Annotated[str, Meta(description="编辑图片的提示词")],
    image_id_list: Annotated[list[str], Meta(description="编辑图片的基础图片ID列表")],
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
):
    """
    调用AI模型进行图片编辑的工具
    """
    if model is None:
        model = random.choice(list(image_edit_workflow.keys()))

    model_func = image_edit_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")

    image_list = [await RM.get(image_id) for image_id in image_id_list]
    result = await model_func(prompt, image_list)
    return result


@ai_tools
async def gen_music(
    style_prompt: Annotated[str, Meta(description="生成音乐的风格提示词")],
    lyric_prompt: Annotated[Optional[str], Meta(description="生成音乐的歌词提示词")] = None,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
):
    """
    调用AI模型进行音乐生成的工具
    """
    if model is None:
        model = random.choice(list(music_workflow.keys()))

    model_func = music_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(style_prompt, lyric_prompt)
    return result


@ai_tools
async def gen_speech(
    text: Annotated[str, Meta(description="生成语音的文本")],
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
):
    """
    调用AI模型进行语音生成的工具
    """
    if model is None:
        model = random.choice(list(speech_workflow.keys()))

    model_func = speech_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(text)
    return result


@ai_tools
async def gen_video_by_text(
    prompt: Annotated[str, Meta(description="生成视频的提示词")],
    w: Annotated[int, Meta(description="生成视频的宽度")] = 720,
    h: Annotated[int, Meta(description="生成视频的高度")] = 1280,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
):
    """
    调用AI模型进行文生视频的工具
    """
    if model is None:
        model = random.choice(list(text2video_workflow.keys()))

    model_func = text2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, w, h)
    return result


@ai_tools
async def gen_video_by_img(
    prompt: Annotated[str, Meta(description="生成视频的提示词")],
    image_id: Annotated[str, Meta(description="生成视频的基础图片ID")],
    w: Annotated[int, Meta(description="生成视频的宽度")] = 720,
    h: Annotated[int, Meta(description="生成视频的高度")] = 1280,
    model: Annotated[Optional[str], Meta(description="使用的模型，为空时默认随机选择")] = None,
):
    """
    调用AI模型进行图生视频的工具
    """
    if model is None:
        model = random.choice(list(image2video_workflow.keys()))

    model_func = image2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    image = await RM.get(image_id)
    result = await model_func(prompt, image, w, h)
    return result
