"""ImageGenerationBase — 图片生成模态基类"""

from __future__ import annotations

from .errors import ValidationError
from .generation import AIGCGenerationBase
from ..schema.request import TaskType, GenerationRequest


class ImageGenerationBase(AIGCGenerationBase):
    """图片生成(文生图 / 图片编辑)模态基类"""

    modality: TaskType = TaskType.IMAGE

    # ── 模态级能力开关 ──
    supports_edit: bool = False  # 是否接受参考图(编辑/重绘)
    max_input_images: int = 0  # 0=纯文生图
    supported_sizes: list[tuple[int, int]] = []  # 空=任意 width/height

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        n_img = len(request.images)
        if n_img > 0 and not self.supports_edit:
            raise ValidationError(f"{self.display_name} 是纯文生图模型,不接受参考图")
        if self.max_input_images and n_img > self.max_input_images:
            raise ValidationError(f"{self.display_name} 最多接受 {self.max_input_images} 张参考图,当前 {n_img} 张")
        if self.supported_sizes and (request.width, request.height) not in self.supported_sizes:
            allowed = ", ".join(f"{w}x{h}" for w, h in self.supported_sizes)
            raise ValidationError(f"{self.display_name} 不支持尺寸 {request.width}x{request.height},可选: {allowed}")

    def normalize(self, request: GenerationRequest) -> GenerationRequest:
        """媒体代号兜底 + 透明参考图合白底。

        ``ensure_media_ref_labels``:有 ordered_content 时把 [@] 剥空后的空洞
        prompt 重建为「[参考图片N]」写法,并在扁平 images 为空时从 OC 回填 ——
        banana / gpt-image-2 / seedream 等只读 prompt+images 的模型统一受益。
        """
        from ...utils.core.media_labels import ensure_media_ref_labels

        request = ensure_media_ref_labels(request)
        if request.images:
            from ...utils.image_process import flatten_transparent_to_white

            request.images = [flatten_transparent_to_white(img) for img in request.images]
        return request


__all__ = ["ImageGenerationBase"]
