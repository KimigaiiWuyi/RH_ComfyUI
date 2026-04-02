"""模型注册表"""

from typing import Dict

from .types import ModelInfo, ModelRequirement


def _create_model_registry() -> Dict[str, ModelInfo]:
    """创建模型注册表"""
    from ..comfyui._request import (
        draw_img_by_qwen_2512,
        gen_music_by_ace_step_1_5,
        gen_speech_by_index_tts_2,
        edit_img_by_qwen_edit_2511,
        gen_video_by_img_by_wan2_2,
        gen_video_by_text_by_wan2_2,
        draw_img_by_img_by_qwen_2512,
    )
    from ...utils.blt.request import (
        edit_img_by_banana2,
        draw_image_by_banana2,
        edit_img_by_banana_pro,
        draw_image_by_banana_pro,
    )

    registry: Dict[str, ModelInfo] = {}

    # ComfyUI 模型 - 需要 ComfyUI 地址
    comfyui_models = [
        (
            "qwen_2512",
            draw_img_by_qwen_2512,
            "text2image",
            "千问Qwen-Image2512",
            "千问Image2512模型，擅长中文提示词理解，适合各种风格的图像生成。优势：中文提示词理解能力强，支持复杂风格描述，输出质量稳定可靠，成本低。适用场景：中文场景的图像生成，简单需求，二次元动漫头像，通用图像生成。",
        ),
        (
            "qwen_2512_img2img",
            draw_img_by_img_by_qwen_2512,
            "image2image",
            "千问Qwen-Image2512 (图生图)",
            "千问Image2512模型，擅长中文提示词理解，适合各种风格的图像生成。优势：中文提示词理解能力强，支持复杂风格描述，输出质量稳定可靠。适用场景：中文场景的图像生成，需要精确描述的画面，艺术创作和插画。",
        ),
        (
            "qwen_2511",
            edit_img_by_qwen_edit_2511,
            "image_edit",
            "通义千问 Edit 2511",
            "专业的图像编辑模型。优势：中文指令理解准确，支持精确的区域编辑，可同时处理多图输入。适用场景：局部修图和编辑，多图融合处理，照片精修，添加或删除元素。",
        ),
        (
            "ace_step1.5",
            gen_music_by_ace_step_1_5,
            "music",
            "ACE Step 1.5",
            "音乐生成模型。优势：可以生成多种风格音乐，支持歌词输入，音乐质量较高。适用场景：背景音乐生成，创意音乐制作，配乐需求。",
        ),
        (
            "IndexTTS2",
            gen_speech_by_index_tts_2,
            "speech",
            "Index TTS 2",
            "语音合成模型。优势：语音自然，中文发音准确，支持多种语气。适用场景：文本转语音，有声内容制作，辅助阅读。",
        ),
        (
            "wan2.2_text2video",
            gen_video_by_text_by_wan2_2,
            "text2video",
            "Wan 2.2 Text2Video",
            "文生视频模型。优势：中文支持良好，可以生成分辨率较高的视频，动作流畅。适用场景：创意视频生成，动画制作，短视频创作。",
        ),
        (
            "wan2.2_img2video",
            gen_video_by_img_by_wan2_2,
            "image2video",
            "Wan 2.2 Image2Video",
            "图生视频模型。优势：可以让静态图片动起来，保持原图风格，中文支持良好。适用场景：让照片变生动，制作循环动画，基于图片的短视频。",
        ),
    ]

    for name, func, task_type, desc, knowledge in comfyui_models:
        registry[name] = ModelInfo(
            name=name,
            func=func,
            requirements=[ModelRequirement.COMFYUI_URL],
            task_type=task_type,  # type: ignore
            description=desc,
            knowledge_content=knowledge,
        )

    # BLT 模型 - 需要 BLT API Key
    blt_models = [
        (
            "banana2",
            draw_image_by_banana2,
            "text2image",
            "Nano Bnana 2",
            "Gemini 3.1 Flash 图像生成模型，速度快，适合快速生成和预览。优势：生成速度非常快，支持快速迭代测试，质量稳定可控，适合批量生成。适用场景：需要较快速度但保持较好质量的图像，高清图像，精细画面。",
        ),
        (
            "banana_pro",
            draw_image_by_banana_pro,
            "text2image",
            "Nano Banana 1 Pro",
            "Nano Banana 2.2K 高质量图像生成模型。优势：图像质量非常高，细节丰富细腻，色彩表现优秀，适合专业输出。适用场景：需要最终输出的高质量图像，专业创作场景，商业项目，需要精细细节的画面。",
        ),
        (
            "banana2_edit",
            edit_img_by_banana2,
            "image_edit",
            "Nano Bnana 2 (编辑)",
            "快速图像编辑模型。优势：处理速度快，适合快速编辑。适用场景：较为复杂的图片修改。",
        ),
        (
            "banana_pro_edit",
            edit_img_by_banana_pro,
            "image_edit",
            "Nano Banana Pro (编辑)",
            "高质量图像编辑模型。优势：编辑质量高，细节处理好。适用场景：精细图片编辑，专业修图，需要高质量输出。",
        ),
    ]

    for name, func, task_type, desc, knowledge in blt_models:
        registry[name] = ModelInfo(
            name=name,
            func=func,
            requirements=[ModelRequirement.BLT_API],
            task_type=task_type,  # type: ignore
            description=desc,
            knowledge_content=knowledge,
        )

    return registry


# 全局模型注册表
MODEL_REGISTRY: Dict[str, ModelInfo] = _create_model_registry()
