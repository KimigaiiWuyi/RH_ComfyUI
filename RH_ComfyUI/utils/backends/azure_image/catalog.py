"""Azure gpt-image 清单。纯数据,配置加载早期会读,不要 import 其它模块。

``name`` 同时是模型 id 与 Foundry 部署名。顺序即控制台排列。
"""

from __future__ import annotations

# (模型 id = Foundry 部署名, 展示名)
AZURE_IMAGE_MODELS: list[tuple[str, str]] = [
    ("gpt-image-2", "GPT-Image-2"),
    ("gpt-image-2.5-sunburst", "GPT-Image-2.5 Sunburst"),
    ("gpt-image-2.5-flare", "GPT-Image-2.5 Flare"),
]

AZURE_IMAGE_MODEL_NAMES: list[str] = [name for name, _title in AZURE_IMAGE_MODELS]

# 默认勾选 2.5 两款;gpt-image-2 仍可手动打开。
AZURE_IMAGE_DEFAULT_MODELS: list[str] = [
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
]

__all__ = [
    "AZURE_IMAGE_MODELS",
    "AZURE_IMAGE_MODEL_NAMES",
    "AZURE_IMAGE_DEFAULT_MODELS",
]
