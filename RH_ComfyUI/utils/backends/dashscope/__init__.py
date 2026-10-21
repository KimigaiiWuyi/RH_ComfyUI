"""阿里云 DashScope 共用配置(HappyHorse / 万相 3.0 / Qwen-Audio / Qwen-Image)

凭证读 SERVICE_CONFIG 的 HappyHorse_*_dashscope 键;模型启用由
``DashScope_Enabled_Models`` 勾选。语音和图像客户端不在这里 import。
"""

from .config import (
    DASHSCOPE_MODEL_WAN30,
    DASHSCOPE_MODEL_OPTIONS,
    DASHSCOPE_DEFAULT_BASE_URL,
    DASHSCOPE_MODEL_HAPPYHORSE,
    DASHSCOPE_MODEL_QWEN_TTS_31,
    DASHSCOPE_MODEL_QWEN_TTS_PLUS,
    DASHSCOPE_MODEL_QWEN_TTS_FLASH,
    ProviderCredentials,
    dashscope_credentials,
    dashscope_disabled_reason,
    is_dashscope_model_enabled,
)

__all__ = [
    "DASHSCOPE_DEFAULT_BASE_URL",
    "DASHSCOPE_MODEL_HAPPYHORSE",
    "DASHSCOPE_MODEL_OPTIONS",
    "DASHSCOPE_MODEL_QWEN_TTS_31",
    "DASHSCOPE_MODEL_QWEN_TTS_FLASH",
    "DASHSCOPE_MODEL_QWEN_TTS_PLUS",
    "DASHSCOPE_MODEL_WAN30",
    "ProviderCredentials",
    "dashscope_credentials",
    "dashscope_disabled_reason",
    "is_dashscope_model_enabled",
]
