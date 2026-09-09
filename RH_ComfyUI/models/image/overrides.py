"""图片模态的编程式模型类 — 跨字段校验与多供应商通道覆写

首个需要跨字段校验的入口:Seedream 5.0 Pro 的像素积约束
(参 ARK Seedream 文档「Seedream 5.0 pro — 方式 2」:
总像素范围 [`1280x720` (921600), `2048x2048x1.1025` (4624220)],
宽高比范围 [1/16, 16])。PortSpec 只能约束单字段上下限,乘积必须
由 validate() 覆盖(参见 utils/core/types.py:84-96 的设计说明)。

模式镜像 models/video/overrides.py(SeedanceVideoModel / Wan22VideoModel)。
"""

from __future__ import annotations

from typing import Optional

from ..bridge import ImagePipelineModel
from ...core.base.errors import ValidationError
from ...core.schema.request import GenerationRequest
from ...utils.core.pipeline import NodeDef


class Seedream5ProImageModel(ImagePipelineModel):
    """Seedream 5.0 Pro:像素积 [921600, 4624220] + 宽高比 [1/16, 16] 双重校验

    当 NodeDef 暴露 width/height 端口(显式像素模式 / 方式 2)时生效;
    若仅暴露 size_mode + ratio(方式 1,ARK 自定像素),无需校验。
    为安全起见,本类同时覆盖宽高比上下界,防止上游返回 4xx 时污染用户。
    """

    # 像素积上下界(单位:像素²),来自 ARK Seedream 5.0 Pro 文档
    MIN_PIXELS = 921600  # 1280*720
    MAX_PIXELS = 4624220  # 2048*2048*1.1025 取整

    # 宽高比上下界(来自同一文档)
    MIN_ASPECT_RATIO = 1 / 16
    MAX_ASPECT_RATIO = 16

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.card = self.card.__class__(  # 复用基类 ModelCard,只覆盖 description
            description=node.description or "Seedream 5.0 Pro 火山方舟高质量图片生成/编辑",
            strengths=[
                "高画质",
                "参考图上限 10 张",
                "支持 1K/2K 档位 + 显式像素",
            ],
            categories=["二次元", "写实", "创意图像", "商品图"],
            speed_hint="normal",
        )

    def validate(self, request: GenerationRequest) -> None:
        # ★ 先跑 schema 通用校验(图像数量等)
        super().validate(request)

        width = int(getattr(request, "width", 0) or 0)
        height = int(getattr(request, "height", 0) or 0)

        # width/height = 0 视为未设置(用户只用了 size_mode 档位方式),不校验
        if width <= 0 or height <= 0:
            return

        pixels = width * height
        if pixels < self.MIN_PIXELS:
            raise ValidationError(
                f"Seedream 5.0 Pro 总像素不得低于 {self.MIN_PIXELS} "
                f"(≈1280×720),当前 {width}×{height}={pixels} 像素过小。"
                "可改用 size_mode 档位(1K/2K)由模型自动选像素,"
                "或改用 Seedream 5.0 Lite(支持 2560×1440 起)。"
            )
        if pixels > self.MAX_PIXELS:
            raise ValidationError(
                f"Seedream 5.0 Pro 总像素不得超过 {self.MAX_PIXELS} "
                f"(≈2048×2048×1.1025),当前 {width}×{height}={pixels} 像素过大。"
                "请缩小 width/height,或改用 Seedream 5.0 Lite。"
            )

        ratio = width / height
        if ratio < self.MIN_ASPECT_RATIO or ratio > self.MAX_ASPECT_RATIO:
            raise ValidationError(
                f"Seedream 5.0 Pro 宽高比需在 [{self.MIN_ASPECT_RATIO:.4f}, "
                f"{self.MAX_ASPECT_RATIO}] 范围内,当前 {width}/{height}={ratio:.4f}。"
            )


class GptImageFamilyModel(ImagePipelineModel):
    """gpt-image-2 / 2.5-sunburst / 2.5-flare:同协议;2.5 多 xhigh/max。"""

    def validate(self, request: GenerationRequest) -> None:
        from ...utils.mappers.gpt_image2_params import (
            gpt_image2_transparent_jpeg,
            resolve_gpt_image2_output_format,
        )

        params = dict(request.params or {})
        params["output_format"] = resolve_gpt_image2_output_format(params)
        request.params = params
        super().validate(request)
        if gpt_image2_transparent_jpeg(request.params):
            raise ValidationError(f"{self.display_name}:透明背景不能使用 jpeg,请改用 png")

    def normalize(self, request: GenerationRequest) -> GenerationRequest:
        from ...utils.mappers.gpt_image2_params import (
            resolve_gpt_image2_background,
            resolve_gpt_image2_output_format,
        )

        params = dict(request.params or {})
        params["background"] = resolve_gpt_image2_background(params)
        params["output_format"] = resolve_gpt_image2_output_format(params)
        request.params = params
        return super().normalize(request)

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按 quality + ratio + image_size 折算 tokens。

        210 元 / 1M tokens;2.5 与 2.0 同单价。缺省 medium + 1024x1024。
        2.5 high 对齐 2.0 medium;2.5 max 对齐 2.0 high。
        """
        from ...utils.mappers.gpt_image2_billing import estimate_gpt_image2_points

        params = request.params
        raw_q = params["quality"] if "quality" in params else None
        quality = raw_q if isinstance(raw_q, str) else None
        raw_sz = params["image_size"] if "image_size" in params else None
        image_size = raw_sz if isinstance(raw_sz, str) else None
        return estimate_gpt_image2_points(quality, request.ratio, image_size, model=self.name)

    def settle_cost(self, request: GenerationRequest, usage: dict) -> Optional[int]:
        """供应商 usage 实扣;无法解析则维持预扣。"""
        from ...utils.mappers.gpt_image2_billing import settle_gpt_image2_points

        del request
        return settle_gpt_image2_points(usage)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(low + 1K) ~ 最大(2.5 max / 2.0 high + 4K)。"""
        from ...utils.mappers.gpt_image2_billing import IMAGE_MODEL_SPECS, estimate_gpt_image2_points

        spec_name = self.name if self.name in IMAGE_MODEL_SPECS else "gpt-image-2"
        factors = IMAGE_MODEL_SPECS[spec_name]["quality_axis_factors"]
        q_hi = "max" if "max" in factors else "high"
        return (
            estimate_gpt_image2_points("low", "1:1", "1K", model=self.name),
            estimate_gpt_image2_points(q_hi, "1:1", "4K", model=self.name),
        )


__all__ = ["Seedream5ProImageModel", "GptImageFamilyModel"]
