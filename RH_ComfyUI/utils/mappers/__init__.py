"""编程式参数映射函数"""

from .music import ace_step_mapper
from .video import wan_img2video_mapper, wan_text2video_mapper
from .speech import index_tts2_mapper
from .image_edit import qwen_edit_mapper
from .image2image import qwen_img2img_mapper
from .blt_image_edit import banana2_edit_mapper, banana_pro_edit_mapper
from .blt_text2image import banana2_mapper, banana_pro_mapper

__all__ = [
    "qwen_img2img_mapper",
    "qwen_edit_mapper",
    "banana2_mapper",
    "banana_pro_mapper",
    "banana2_edit_mapper",
    "banana_pro_edit_mapper",
    "wan_text2video_mapper",
    "wan_img2video_mapper",
    "ace_step_mapper",
    "index_tts2_mapper",
]
