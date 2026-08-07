"""Seedance 供应商无关的视频生成意图规格

`VideoGenSpec` 把"用户想要什么"与"各家供应商如何表达"彻底解耦:
- `VideoTaskShape`: 任务形态(T2V / I2V / 首尾帧 / 多模态 / 编辑 / 续写)
- `MediaRole`: 媒体在任务中的语义角色(首帧 / 尾帧 / 参考)
- `SpecMedia`: 带角色与序号的单条媒体
- `VideoGenSpec`: 完整的归一化请求,被分类器产出、被各家 Provider 消费
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from dataclasses import field, dataclass

from ...core.types import MediaRef, MediaKind


class VideoTaskShape(str, Enum):
    """供应商无关的任务形态"""

    TEXT2VIDEO = "text2video"
    IMAGE2VIDEO = "image2video"
    FIRST_LAST_FRAME = "first_last_frame"
    MULTIMODAL = "multimodal"
    VIDEO_EDIT = "video_edit"
    VIDEO_EXTEND = "video_extend"


class MediaRole(str, Enum):
    """媒体在任务中的语义角色"""

    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    REFERENCE = "reference"


@dataclass
class SpecMedia:
    """带角色与序号的一条媒体"""

    kind: MediaKind
    role: MediaRole
    ref: MediaRef
    index: int


@dataclass
class OrderedSegment:
    """`ordered_content` 的一项,保留"text + media"交错顺序。

    `text` 与 `media` 互斥:`kind == "text"` 时 `text` 有效,`media` 为 None;
    `kind == "media"` 时 `media` 有效,`text` 为 None。
    """

    kind: str  # "text" | "media"
    text: Optional[str] = None
    media: Optional[SpecMedia] = None


@dataclass
class VideoGenSpec:
    """供应商无关的视频生成意图"""

    shape: VideoTaskShape
    prompt: str
    media: list[SpecMedia]

    # `ordered_content` 解析后的"文本+媒体"交错序列,按原顺序保留。
    # 支持多模态顺序的供应商(content[] 模型)按它渲染,可保留每段文本
    # 出现在它所引用的图片之间的位置关系。
    # 没有 ordered_content 的老路径保持为空,Provider 退回到 prompt+media。
    ordered_segments: list[OrderedSegment] = field(default_factory=list)

    ratio: Optional[str] = None
    resolution: Optional[str] = None
    # 输出时长(秒)。Seedance 2.5 视频编辑/延长支持 -1=跟随输入视频时长。
    duration: int = 5
    seed: Optional[int] = None
    generate_audio: bool = False
    watermark: bool = False  # 默认关,与 GenerationRequest / 各模型 input_schema 对齐
    camera_fixed: bool = False
    return_last_frame: bool = False
    service_tier: str = "default"
    # 输出容器格式(Seedance 2.5 支持 mp4 / mov;2.0 仅 mp4 时忽略)
    output_format: Optional[str] = None

    # 供应商私有旁路:real_person_mode / conversion_slots / draft / callback_url / ...
    params: dict[str, Any] = field(default_factory=dict)

    # ── 便捷视图 ──

    def images(self) -> list[SpecMedia]:
        return [m for m in self.media if m.kind == MediaKind.IMAGE]

    def videos(self) -> list[SpecMedia]:
        return [m for m in self.media if m.kind == MediaKind.VIDEO]

    def audios(self) -> list[SpecMedia]:
        return [m for m in self.media if m.kind == MediaKind.AUDIO]

    def first_frame(self) -> Optional[SpecMedia]:
        return next((m for m in self.media if m.role == MediaRole.FIRST_FRAME), None)

    def last_frame(self) -> Optional[SpecMedia]:
        return next((m for m in self.media if m.role == MediaRole.LAST_FRAME), None)


__all__ = [
    "VideoTaskShape",
    "MediaRole",
    "SpecMedia",
    "OrderedSegment",
    "VideoGenSpec",
]
