"""图生图映射函数(ComfyUI 工作流)

用于 Qwen-Image 2512 等模型的图生图场景:
用户上传参考图片,模型在参考图基础上进行重绘/风格化。
"""

from __future__ import annotations

from typing import Any

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI


async def qwen_img2img_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """千问图生图工作流的参数映射

    工作流节点(qwen_2512_with_lora.json):
      - 节点 23: CLIPTextEncode (prompt)
      - 节点 41: LoadImage (参考图输入)
      - 节点 26: KSampler (denoise 控制重绘强度)
    """
    # 设置 prompt
    workflow["23"]["inputs"]["text"] = request.prompt

    # 上传参考图片并设置到 LoadImage 节点
    if request.images:
        uploaded = await api.upload_image(request.images[0])
        workflow["41"]["inputs"]["image"] = uploaded

    # 设置 denoise 强度(可通过 params 传入,默认 0.4)
    denoise = request.params.get("denoise", 0.4)
    workflow["26"]["inputs"]["denoise"] = denoise

    # 如果有负面提示词
    if request.negative_prompt:
        workflow["24"]["inputs"]["text"] = request.negative_prompt

    return workflow
