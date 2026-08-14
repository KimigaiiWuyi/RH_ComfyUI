"""阿里云 DashScope HappyHorse 视频生成后端

对外暴露统一模型 ``happyhorse1.1``;内部按输入自动路由到:
- happyhorse-1.1-t2v          文生视频(0 图)
- happyhorse-1.1-i2v          图生视频首帧(1 图 / frame_mode=first_frame)
- happyhorse-1.1-r2v          参考生视频(多图 / frame_mode=reference;不接受视频)
- happyhorse-1.1-video-edit   视频编辑(显式 task_mode=edit,唯一可传视频的形态)

API:
- POST /api/v1/services/aigc/video-generation/video-synthesis  (X-DashScope-Async: enable)
- GET  /api/v1/tasks/{task_id}
"""

from .channel import HappyHorseChannel, builtin_happyhorse_channels
from .classify import classify_happyhorse, resolve_vendor_model

__all__ = [
    "HappyHorseChannel",
    "builtin_happyhorse_channels",
    "classify_happyhorse",
    "resolve_vendor_model",
]
