from ._request import draw_img_by_qwen_2512

MODEL_MAP = {
    "qwen_2512": draw_img_by_qwen_2512,
}


async def text_to_image(model: str, prompt: str):
    model_func = MODEL_MAP.get(model)
    if model_func is None:
        raise ValueError(f"模型 {model} 不存在")
    image = await model_func(prompt)
    return image


async def image_to_image(model: str, prompt: str, image: bytes):
    pass
