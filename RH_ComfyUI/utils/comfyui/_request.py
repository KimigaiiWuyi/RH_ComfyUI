from typing import List, Optional

from .comfyui_api import api
from ..resource.RESOURCE_PATH import (
    EDIT_WORKFLOW_PATH,
    MUSIC_WORKFLOW_PATH,
    SPEECH_WORKFLOW_PATH,
    DRAW_TEXT_WORKFLOW_PATH,
    DRAW_IMAGE_WORKFLOW_PATH,
    VIDEO_BY_TEXT_WORKFLOW_PATH,
    VIDEO_BY_IMAGE_WORKFLOW_PATH,
    load_workflow,
)


async def draw_img_by_qwen_2512(
    prompt: str,
    w: int = 720,
    h: int = 1280,
):
    workflow = load_workflow(DRAW_TEXT_WORKFLOW_PATH / "qwen_2512.json")
    workflow["108"]["inputs"]["text"] = prompt
    workflow["107"]["inputs"]["width"] = w
    workflow["107"]["inputs"]["height"] = h
    image = await api.generate_image_by_prompt(workflow)
    return image


async def draw_img_by_img_by_qwen_2512(prompt: str, input_image: bytes):
    workflow = load_workflow(DRAW_IMAGE_WORKFLOW_PATH / "qwen_2512_with_lora.json")
    workflow["23"]["inputs"]["text"] = prompt
    workflow["41"]["inputs"]["image"] = await api.upload_image(input_image)
    image = await api.generate_image_by_prompt(workflow)
    return image


async def edit_img_by_qwen_edit_2511(prompt: str, img_list: List[bytes]):
    workflow = load_workflow(EDIT_WORKFLOW_PATH / "qwen_edit_2511.json")
    workflow["103"]["inputs"]["text"] = prompt

    img_slot = ["41", "79", "81"]
    img_slot2 = ["73", "79", "81"]
    for index, i in enumerate(img_list):
        if index >= 3:
            break

        workflow[img_slot[index]]["inputs"]["image"] = await api.upload_image(i)

        for j in ["68", "69"]:
            workflow[j]["inputs"][f"image{index + 1}"] = [img_slot2[index], 0]

    image = await api.generate_image_by_prompt(workflow)
    return image


async def gen_music_by_ace_step_1_5(style_prompt: str, lyric_prompt: Optional[str] = None):
    workflow = load_workflow(MUSIC_WORKFLOW_PATH / "ace_step1.5.json")
    workflow["131"]["inputs"]["text"] = style_prompt
    workflow["130"]["inputs"]["text"] = lyric_prompt if lyric_prompt else ""

    audio = await api.generate_audio_by_prompt(workflow)
    return audio


async def gen_speech_by_index_tts_2(text: str):
    workflow = load_workflow(SPEECH_WORKFLOW_PATH / "IndexTTS2.json")
    workflow["14"]["inputs"]["value"] = text

    audio = await api.generate_audio_by_prompt(workflow)
    return audio


async def gen_video_by_text_by_wan2_2(
    text: str,
    w: int = 720,
    h: int = 1280,
    duration: int = 5,
):
    workflow = load_workflow(VIDEO_BY_TEXT_WORKFLOW_PATH / "wan2.2_text2video.json")
    workflow["37"]["inputs"]["text"] = text
    workflow["44"]["inputs"]["value"] = w
    workflow["34"]["inputs"]["value"] = h
    workflow["33"]["inputs"]["value"] = duration

    video = await api.generate_video_by_prompt(workflow)
    return video


async def gen_video_by_img_by_wan2_2(
    text: str,
    img: bytes,
    w: int = 720,
    h: int = 1280,
    duration: int = 5,
):
    workflow = load_workflow(VIDEO_BY_IMAGE_WORKFLOW_PATH / "wan2.2_image2video.json")
    workflow["102"]["inputs"]["text"] = text
    workflow["289"]["inputs"]["value"] = w
    workflow["290"]["inputs"]["value"] = h
    workflow["67"]["inputs"]["image"] = await api.upload_image(img)
    workflow["294"]["inputs"]["value"] = duration

    video = await api.generate_video_by_prompt(workflow)
    return video
