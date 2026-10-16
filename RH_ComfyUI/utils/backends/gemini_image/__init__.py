"""gemini_image — Gemini generate_content 生图通道。

挂在 banana1 / banana2 / banana2.1 / banana2.lite / banana_pro 上。双模由 Gemini_Image_Use_Vertex 决定:
关=AI Studio+key,开=VertexAI+ADC/SA。Gemini_Enabled_Models 勾选后才走本通道;
banana_pro 的 gpt-image-2 不受该列表影响。凭证读 SERVICE_CONFIG,改完即时生效。
"""

from .config import GEMINI_MODEL_OPTIONS, is_gemini_model_enabled

__all__ = ["GEMINI_MODEL_OPTIONS", "is_gemini_model_enabled"]


def __getattr__(name: str) -> object:
    # 包入口不 import channel:service_config 读名单时会经本包,
    # channel→api→SERVICE_CONFIG 会在配置建完前形成循环。
    if name == "GeminiImageChannel":
        from .channel import GeminiImageChannel

        return GeminiImageChannel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
