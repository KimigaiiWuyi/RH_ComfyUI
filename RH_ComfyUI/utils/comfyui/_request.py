from typing import List

from .comfyui_api import api
from ..resource.RESOURCE_PATH import (
    EDIT_WORKFLOW_PATH,
    MUSIC_WORKFLOW_PATH,
    DRAW_TEXT_WORKFLOW_PATH,
    DRAW_IMAGE_WORKFLOW_PATH,
    load_workflow,
)


async def draw_img_by_qwen_2512(prompt: str):
    workflow = load_workflow(DRAW_TEXT_WORKFLOW_PATH / "qwen_2512.json")
    workflow["108"]["inputs"]["text"] = prompt
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
    for index, i in enumerate(img_list):
        if index >= 3:
            break

        workflow[img_slot[index]]["inputs"]["image"] = await api.upload_image(i)

    image = await api.generate_image_by_prompt(workflow)
    return image


async def gen_music(style_prompt: str, lyric_prompt: str):
    workflow = load_workflow(MUSIC_WORKFLOW_PATH / "ace_step1.5.json")
    workflow["125"]["inputs"]["text"] = style_prompt
    workflow["126"]["inputs"]["text"] = lyric_prompt

    audio = await api.generate_audio_by_prompt(workflow)
    return audio
