"""core.schema.types — 类型系统的规范导入路径

迁移期实现:类型定义仍物理存放在 utils/core/types.py(大量存量代码引用它),
本模块 re-export 为规范路径。阶段 5 清理时把实现搬到这里、utils 变 shim。
"""

from __future__ import annotations

from typing import Callable, Optional, Awaitable

from ...utils.core.types import (
    MediaRef,
    PortSpec,
    PortType,
    MediaKind,
    NodeOutput,
    ContentItem,
    ProgressEvent,
    ContentItemType,
    CapabilityManifest,
    audio_ref,
    image_ref,
    text_item,
    video_ref,
    audio_item,
    image_item,
    video_item,
    draft_task_item,
)

# 进度回调:async 回调(与 utils.backends.base.ProgressCallback 语义一致)
ProgressCallback = Callable[[ProgressEvent], Awaitable[Optional[None]]]

__all__ = [
    "PortType",
    "MediaKind",
    "ContentItemType",
    "PortSpec",
    "MediaRef",
    "ContentItem",
    "CapabilityManifest",
    "ProgressEvent",
    "NodeOutput",
    "ProgressCallback",
    "image_ref",
    "video_ref",
    "audio_ref",
    "text_item",
    "image_item",
    "video_item",
    "audio_item",
    "draft_task_item",
]
