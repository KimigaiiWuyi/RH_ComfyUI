"""可灵模型清单。纯数据,配置加载早期会读,不要 import 其它模块。

顺序与控制台下拉一致:视频五行,图片四行。
"""

from __future__ import annotations

# (模型 id, 展示名)
KLING_VIDEO_MODELS: list[tuple[str, str]] = [
    ("kling-4.0", "Kling 4.0 & 4.0 Omni"),
    ("kling-3.0-turbo", "Kling 3.0 Turbo"),
    ("kling-3.0", "Kling 3.0 & 3.0 Omni"),
    ("kling-o1", "Kling O1"),
    ("kling-2.6", "Kling 2.6"),
    ("kling-2.5-turbo", "Kling 2.5 Turbo"),
]

KLING_IMAGE_MODELS: list[tuple[str, str]] = [
    ("kling-image-3.0", "Kling Image 3.0 & 3.0 Omni"),
    ("kling-image-o1", "Kling Image O1"),
    ("kling-image-common", "通用"),
    ("kling-image-2.1", "Kling Image 2.1"),
]

KLING_MODEL_NAMES: list[str] = [name for name, _title in (*KLING_VIDEO_MODELS, *KLING_IMAGE_MODELS)]

# 新视频任务查询走 GET /tasks。图片沿用创建路径 + /{id}。
# 任务号前缀让恢复轮询知道查哪条路径。
POLL_TASKS = "v"
POLL_IMAGE_GENERATIONS = "ig"
POLL_IMAGE_OMNI = "io"
POLL_IMAGE_MULTI = "im"
POLL_IMAGE_EXPAND = "ex"
POLL_IMAGE_SUBJECT = "ms"

POLL_PATHS: dict[str, str] = {
    POLL_TASKS: "/tasks",
    POLL_IMAGE_GENERATIONS: "/v1/images/generations",
    POLL_IMAGE_OMNI: "/v1/images/omni-image",
    POLL_IMAGE_MULTI: "/v1/images/multi-image2image",
    POLL_IMAGE_EXPAND: "/v1/images/editing/expand",
    POLL_IMAGE_SUBJECT: "/v1/general/ai-multi-shot",
}

DEFAULT_ENDPOINT = "https://api-beijing.klingai.com"
SINGAPORE_ENDPOINT = "https://api-singapore.klingai.com"

VIDEO_RATIOS: list[str] = ["16:9", "9:16", "1:1"]
# 4.0 文生/Omni 多 21:9;adaptive 仅 Omni 与文生走 Omni 兜底时可用。
VIDEO_RATIOS_40: list[str] = ["16:9", "9:16", "1:1", "21:9", "adaptive"]
IMAGE_RATIOS: list[str] = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"]
IMAGE_RATIOS_WITH_AUTO: list[str] = [*IMAGE_RATIOS, "auto"]

__all__ = [
    "DEFAULT_ENDPOINT",
    "IMAGE_RATIOS",
    "IMAGE_RATIOS_WITH_AUTO",
    "KLING_IMAGE_MODELS",
    "KLING_MODEL_NAMES",
    "KLING_VIDEO_MODELS",
    "POLL_IMAGE_EXPAND",
    "POLL_IMAGE_GENERATIONS",
    "POLL_IMAGE_MULTI",
    "POLL_IMAGE_OMNI",
    "POLL_IMAGE_SUBJECT",
    "POLL_PATHS",
    "POLL_TASKS",
    "SINGAPORE_ENDPOINT",
    "VIDEO_RATIOS",
    "VIDEO_RATIOS_40",
]
