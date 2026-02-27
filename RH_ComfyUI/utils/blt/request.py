from typing import List, Literal

from .blt_request import draw_image_by_blt


def _calculate_aspect_ratio(w: int, h: int) -> Literal["1:1", "4:3", "16:9", "9:16", "3:4", "21:9"]:
    """
    根据宽度和高度自动计算最接近的宽高比

    支持的宽高比: 1:1, 4:3, 3:4, 16:9, 9:16, 21:9

    Args:
        w: 宽度
        h: 高度

    Returns:
        最接近的宽高比字符串
    """
    # 计算实际宽高比
    actual_ratio = w / h

    # 定义支持的宽高比及其对应的值
    ratios = {
        "21:9": 21 / 9,  # 2.333
        "16:9": 16 / 9,  # 1.778
        "4:3": 4 / 3,  # 1.333
        "1:1": 1 / 1,  # 1.0
        "3:4": 3 / 4,  # 0.75
        "9:16": 9 / 16,  # 0.563
    }

    # 找到最接近的宽高比
    closest_ratio = min(ratios.keys(), key=lambda k: abs(ratios[k] - actual_ratio))
    return closest_ratio  # type: ignore


async def draw_image_by_banana2(
    prompt: str,
    w: int = 720,
    h: int = 1280,
):
    # 自动计算最接近的宽高比
    ratio = _calculate_aspect_ratio(w, h)
    return await draw_image_by_blt(
        model="gemini-3.1-flash-image-preview",
        prompt=prompt,
        aspect_ratio=ratio,
    )


async def draw_image_by_banana_pro(
    prompt: str,
    w: int = 720,
    h: int = 1280,
):
    # 自动计算最接近的宽高比
    ratio = _calculate_aspect_ratio(w, h)
    return await draw_image_by_blt(
        model="nano-banana-2-2k",
        prompt=prompt,
        aspect_ratio=ratio,
    )


async def edit_img_by_banana2(prompt: str, img_list: List[bytes]):
    return await draw_image_by_blt(
        model="gemini-3.1-flash-image-preview",
        prompt=prompt,
        aspect_ratio=None,
        image_list=img_list,
    )


async def edit_img_by_banana_pro(prompt: str, img_list: List[bytes]):
    return await draw_image_by_blt(
        model="nano-banana-2-2k",
        prompt=prompt,
        aspect_ratio=None,
        image_list=img_list,
    )
