"""编程式参数映射函数"""

from .music import ace_step_mapper
from .video import wan_img2video_mapper, wan_text2video_mapper
from .speech import index_tts2_mapper
from .image_edit import qwen_edit_mapper
from .image2image import qwen_img2img_mapper
from .mimo_speech import mimo_tts_mapper
from .blt_image_edit import banana2_edit_mapper, banana_pro_edit_mapper
from .blt_text2image import banana2_mapper, banana_pro_mapper
from .minimax_speech import minimax_t2a_speech_mapper
from .minimax_text2image import minimax_image01_mapper
from .minimax_image2image import minimax_image01_img2img_mapper

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
    "minimax_image01_mapper",
    "minimax_image01_img2img_mapper",
    "minimax_t2a_speech_mapper",
    "mimo_tts_mapper",
]
