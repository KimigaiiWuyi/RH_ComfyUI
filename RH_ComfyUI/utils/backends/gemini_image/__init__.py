"""gemini_image — Gemini Interactions API 生图通道。

挂在 banana1 / banana2 / banana_pro 上。双模由 Gemini_Image_Use_Vertex 决定:
关=AI Studio+key,开=VertexAI+ADC/SA。Gemini_Enabled_Models 勾选后才走本通道;
banana_pro 的 gpt-image-2 不受该列表影响。凭证读 SERVICE_CONFIG,改完即时生效。
"""

from .config import GEMINI_MODEL_OPTIONS, is_gemini_model_enabled
from .channel import GeminiImageChannel

__all__ = ["GeminiImageChannel", "GEMINI_MODEL_OPTIONS", "is_gemini_model_enabled"]
