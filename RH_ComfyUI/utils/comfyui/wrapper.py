import random
from typing import Optional

from ._request import (
    draw_img_by_qwen_2512,
    gen_music_by_ace_step_1_5,
    gen_speech_by_index_tts_2,
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


async def gen_image_by_text(
    prompt: str,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    if model is None:
        model = random.choice(list(text2image_workflow.keys()))

    model_func = text2image_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, w, h)
    return result


async def gen_image_by_img(
    prompt: str,
    image: bytes,
    model: Optional[str] = None,
):
    if model is None:
        model = random.choice(list(image2image_workflow.keys()))

    model_func = image2image_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, image)
    return result


async def gen_music(
    style_prompt: str,
    lyric_prompt: Optional[str] = None,
    model: Optional[str] = None,
):
    if model is None:
        model = random.choice(list(music_workflow.keys()))

    model_func = music_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(style_prompt, lyric_prompt)
    return result


async def gen_speech(
    text: str,
    model: Optional[str] = None,
):
    if model is None:
        model = random.choice(list(speech_workflow.keys()))

    model_func = speech_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(text)
    return result


async def gen_video_by_text(
    prompt: str,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    if model is None:
        model = random.choice(list(text2video_workflow.keys()))

    model_func = text2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, w, h)
    return result


async def gen_video_by_img(
    prompt: str,
    image: bytes,
    w: int = 720,
    h: int = 1280,
    model: Optional[str] = None,
):
    if model is None:
        model = random.choice(list(image2video_workflow.keys()))

    model_func = image2video_workflow.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    result = await model_func(prompt, image, w, h)
    return result
