"""模型优先级定义"""

from typing import List, Optional

MODEL_PRIORITY = {
    "text2image": ["qwen_2512", "banana2", "banana_pro"],
    "image2image": ["qwen_2512_img2img"],
    "image_edit": ["qwen_2511", "banana2", "banana_pro"],
    "text2video": ["wan2.2_text2video"],
    "image2video": ["wan2.2_img2video"],
    "music": ["ace_step1.5"],
    "speech": ["IndexTTS2"],
}


def _get_priority_model(available_models: List[str], category: str) -> Optional[str]:
    """根据优先级从可用模型中选择"""
    priority_list = MODEL_PRIORITY.get(category, [])
    for model_name in priority_list:
        if model_name in available_models:
            return model_name
    return None
