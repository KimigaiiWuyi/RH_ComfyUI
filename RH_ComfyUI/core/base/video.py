"""VideoGenerationBase — 视频生成模态基类

统一提供:
- 任务形态分类(shape_of():文生/图生/首尾帧/多模态),复用 seedance 的形态枚举
- 视频输入预处理(参考图等比缩放,复用 utils.image_process)
- 通用视频端口构造器(子类拼装 input_schema 时少写样板)
"""

from __future__ import annotations

from .errors import ValidationError
from .generation import AIGCGenerationBase
from ..schema.types import PortSpec, PortType
from ..schema.request import TaskType, GenerationRequest

# 形态枚举直接复用 backends/seedance/spec.py 的 VideoTaskShape
from ...utils.backends.seedance.spec import VideoTaskShape


class VideoGenerationBase(AIGCGenerationBase):
    """视频生成模态基类"""

    modality: TaskType = TaskType.VIDEO

    # ── 模态级能力开关(子类按事实声明,驱动通用校验) ──
    supported_shapes: set[VideoTaskShape] = {VideoTaskShape.TEXT2VIDEO}
    supported_resolutions: list[str] = ["480p", "720p"]
    supported_ratios: list[str] = []  # 空 = 不限(任意 width/height)
    supports_generate_audio: bool = False
    max_reference_total: int = 0  # 参考素材总量上限(多模态模型用)

    # ── 通用派生 ──

    def shape_of(self, request: GenerationRequest) -> VideoTaskShape:
        """按输入自动判定任务形态(0 图=T2V / 1 图=I2V / 2 图=FLF / 含音视频=多模态)"""
        if request.ordered_content or request.video_refs or request.audio_refs:
            return VideoTaskShape.MULTIMODAL
        n = len(request.images)
        if n == 0:
            return VideoTaskShape.TEXT2VIDEO
        if n == 1:
            return VideoTaskShape.IMAGE2VIDEO
        if n == 2:
            return VideoTaskShape.FIRST_LAST_FRAME
        return VideoTaskShape.MULTIMODAL

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        shape = self.shape_of(request)
        if shape not in self.supported_shapes:
            raise ValidationError(
                f"{self.display_name} 不支持当前输入形态({shape.value});"
                f"支持: {', '.join(s.value for s in self.supported_shapes)}"
            )
        if request.resolution and self.supported_resolutions:
            if request.resolution.lower() not in self.supported_resolutions:
                allowed = ", ".join(self.supported_resolutions)
                raise ValidationError(f"{self.display_name} 不支持分辨率 {request.resolution},可选: {allowed}")
        if request.ratio and self.supported_ratios and request.ratio not in self.supported_ratios:
            raise ValidationError(
                f"{self.display_name} 不支持宽高比 {request.ratio},可选: {', '.join(self.supported_ratios)}"
            )
        if request.generate_audio and not self.supports_generate_audio:
            raise ValidationError(f"{self.display_name} 不支持有声视频(generate_audio)")
        if self.max_reference_total:
            total = len(request.images) + len(request.video_refs) + len(request.audio_refs)
            if total > self.max_reference_total:
                raise ValidationError(
                    f"参考素材共 {total} 个,超过 {self.display_name} 上限 {self.max_reference_total} 个"
                )

    def normalize(self, request: GenerationRequest) -> GenerationRequest:
        """视频通用预处理:分辨率统一小写 + 参考图等比缩放/EXIF 校正"""
        if request.resolution:
            request.resolution = request.resolution.lower()
        from ...utils.image_process import preprocess_for_video

        if request.images:
            request.images = [preprocess_for_video(img) for img in request.images]
        return request

    # ── 端口构造器:子类拼 input_schema 的积木 ──

    @staticmethod
    def prompt_port(*, required: bool = True, description: str = "视频生成提示词") -> PortSpec:
        return PortSpec(type=PortType.TEXT, required=required, description=description)

    @staticmethod
    def images_port(*, max_items: int, description: str) -> PortSpec:
        return PortSpec(
            type=PortType.LIST,
            item_type=PortType.IMAGE,
            min_items=0,
            max_items=max_items,
            description=description,
        )

    @staticmethod
    def duration_port(*, minimum: int, maximum: int, default: int = 5) -> PortSpec:
        return PortSpec(
            type=PortType.INTEGER,
            default=default,
            minimum=minimum,
            maximum=maximum,
            description="视频时长(秒)",
        )


__all__ = ["VideoGenerationBase", "VideoTaskShape"]
