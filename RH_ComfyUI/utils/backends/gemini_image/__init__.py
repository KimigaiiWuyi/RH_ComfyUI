"""gemini_image — Gemini Interactions API 生图通道(gemini-3.1-flash-image-preview)

不是独立后端/模型:作为 banana2(Nano Banana 2)的一路 ProviderChannel,与
OpenAI 兼容网关一起负载均衡。单一 key 双模:填 Project ID 走 VertexAI(Bearer),
留空走 AI Studio(x-goog-api-key)。凭证读 SERVICE_CONFIG,改完即时生效。
"""

from .channel import GeminiImageChannel

__all__ = ["GeminiImageChannel"]
