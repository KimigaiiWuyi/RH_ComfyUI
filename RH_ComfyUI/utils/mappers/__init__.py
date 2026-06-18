"""编程式参数映射函数"""

from .music import ace_step_mapper
from .video import (
    wan_videogen_mapper,
    wan_img2video_mapper,
    wan_text2video_mapper,
    interpolate_prompt_refs,
)
from .speech import index_tts2_mapper
from .seedance import (
    seedance_draft_mapper,
    seedance_video_mapper,
    seedance_text2video_mapper,
)
from .gpt_image2 import gpt_image2_mapper
from .image_edit import qwen_edit_mapper
from .mimo_speech import mimo_tts_mapper
from .minimax_speech import minimax_t2a_speech_mapper
from .minimax_text2image import minimax_image01_mapper

__all__ = [
    # ── 图片(文生图 + 编辑,按输入形状自适应;走 gpt_image2 / OpenAI 兼容后端) ──
    "gpt_image2_mapper",
    "minimax_image01_mapper",
    # ── ComfyUI 声明式 mapper (图片编辑) ──
    "qwen_edit_mapper",
    # ── 视频(ComfyUI Wan 统一 videogen mapper) ──
    "wan_videogen_mapper",
    "wan_text2video_mapper",
    "wan_img2video_mapper",
    "interpolate_prompt_refs",
    # ── 视频(Seedance 统一 mapper 按输入分发;draft 由 request.params["draft"]=True 表达) ──
    "seedance_video_mapper",
    "seedance_text2video_mapper",
    "seedance_draft_mapper",
    # ── 音乐 ──
    "ace_step_mapper",
    # ── 语音 ──
    "index_tts2_mapper",
    "minimax_t2a_speech_mapper",
    "mimo_tts_mapper",
]
