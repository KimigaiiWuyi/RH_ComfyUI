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
from collections.abc import Mapping

from ..bridge import ImagePipelineModel
from ...core.base.errors import ValidationError
from ...core.schema.card import ModelCard
from ...core.schema.types import MediaKind
from ...core.schema.request import GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.family import lock_bindings_to_provider_family
from ...core.channels.channel import ChannelBinding
from ...core.channels.registry import channel_registry


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
    """gpt-image 家族(2.0 / 2.5 / 2.6 sunburst / flare):同协议;2.5+ 多 xhigh/max,2.6 多 gif。"""

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


class MiniMaxH3ImageModel(ImagePipelineModel):
    """MiniMax H3-Image:官方 / fal 双通道,文生图 + 图片编辑(≤16 参考图)。

    模型与供应商正交:channel_bindings 挂官方 minimax-h3-image 通道 +
    fal 通道 + 外部插件注入;跨字段约束(编辑模式恒增强、n=1)在本类收口。
    """

    MAX_IMAGES = 16
    MAX_PROMPT_CHARS = 32000
    VENDOR_MODEL = "MiniMax-H3-Image"
    VENDOR_MODEL_HIGHSPEED = "MiniMax-H3-Image-Highspeed"

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.card = ModelCard(
            description=node.description or "MiniMax H3-Image 文生图与多图编辑,最高 4K/20MP",
            strengths=[
                "文生图与 1~16 图编辑同节点",
                "1K/2K/4K 档位或显式像素尺寸",
                "宽高比 auto 或 1:3~3:1 任意比例",
                "官方 MiniMax 与 fal 双通道正交",
            ],
            categories=["写实", "商品图", "海报", "多图合成", "局部编辑"],
            weaknesses=[
                "编辑模式提示词恒被增强,不能关",
                "fal 通道文生图不支持 auto 宽高比 / 水印 / highspeed",
            ],
            sample_prompts=[
                "一杯拿铁放在原木桌上,俯拍,暖光",
                "把图片1里的猫换成图片2里的狗,其余保持不变",
            ],
            languages=["zh", "en"],
            speed_hint="normal",
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        prompt = (request.prompt or "").strip()
        if not prompt:
            raise ValidationError(f"{self.display_name} 必须填写提示词")
        if len(prompt) > self.MAX_PROMPT_CHARS:
            raise ValidationError(f"{self.display_name} 提示词最长 {self.MAX_PROMPT_CHARS} 字符,当前 {len(prompt)}")
        if request.video_refs or request.audio_refs:
            raise ValidationError(f"{self.display_name} 是生图模型,不接受音视频参考素材")
        n_img = len(request.images)
        if request.ordered_content:
            n_oc = 0
            for item in request.ordered_content:
                if item.media is None:
                    continue
                if item.media.kind != MediaKind.IMAGE:
                    raise ValidationError(f"{self.display_name} 只接受图片素材,实际收到 {item.media.kind.value}")
                n_oc += 1
            n_img = max(n_img, n_oc)
        if n_img > self.MAX_IMAGES:
            raise ValidationError(f"{self.display_name} 最多 {self.MAX_IMAGES} 张参考图,当前 {n_img} 张")
        # 编辑模式两家上游都恒开提示词增强,关掉会静默失效 → 请求级拒绝
        if n_img and _is_false_param(request, "prompt_enhanced"):
            raise ValidationError(f"{self.display_name} 的图片编辑模式始终开启提示词增强,prompt_enhanced 不能为否")
        if "n" in request.params:
            n_raw: object = request.params["n"]
            if n_raw is not None:
                try:
                    n_val = int(str(n_raw))
                except (TypeError, ValueError):
                    raise ValidationError(f"{self.display_name} 的 n 必须是整数,实际收到 {n_raw!r}") from None
                if n_val != 1:
                    raise ValidationError(f"{self.display_name} 单次返回 1 张图,不支持 n={n_val}")

    def channel_bindings(self) -> list[ChannelBinding]:
        from ...utils.backends.minimax.h3_image_channel import builtin_minimax_h3_image_channels

        node = self.node
        builtins = builtin_minimax_h3_image_channels()
        external = channel_registry.bindings_for(node.name)
        if node.provider:
            return lock_bindings_to_provider_family(
                node.provider,
                builtins,
                external,
                builtin_vendor_model=self.VENDOR_MODEL,
            )
        bindings: list[ChannelBinding] = [
            ChannelBinding(ch, vendor_model=self.VENDOR_MODEL) for ch in builtins.values()
        ]
        bindings.extend(external)
        return bindings

    async def unavailable_reason(self) -> str:
        for binding in self.channel_bindings():
            if await binding.channel.check_available():
                continue
            return await binding.channel.unavailable_reason()
        return f"{self.display_name} 无可用供应商"

    def estimate_cost(self, request: GenerationRequest) -> int:
        """token 计费曲线:文本 4000 + 输入图 3000/张 + 输出按档位/像素折算。"""
        from ...utils.mappers.minimax_h3_image_billing import estimate_minimax_h3_image_points

        params = request.params
        image_size = params["image_size"] if "image_size" in params else None
        variant = params["model_variant"] if "model_variant" in params else "standard"
        width, height = _explicit_size_params(params)
        n_img = len(request.images)
        if request.ordered_content:
            n_oc = sum(
                1 for item in request.ordered_content if item.media is not None and item.media.kind == MediaKind.IMAGE
            )
            n_img = max(n_img, n_oc)
        return estimate_minimax_h3_image_points(image_size, n_img, variant=variant, width=width, height=height)

    def settle_cost(self, request: GenerationRequest, usage: dict) -> Optional[int]:
        """上游 usage 实报 token 实扣;解析不出则维持预扣。"""
        from ...utils.mappers.minimax_h3_image_billing import settle_minimax_h3_image_points

        del request
        return settle_minimax_h3_image_points(usage)

    def point_range(self) -> tuple[int, int]:
        """最小 1K 文生(标准);最大 4K + 16 参考图(highspeed)。"""
        from ...utils.mappers.minimax_h3_image_billing import estimate_minimax_h3_image_points

        return (
            estimate_minimax_h3_image_points("1K", 0, variant="standard"),
            estimate_minimax_h3_image_points("4K", self.MAX_IMAGES, variant="highspeed"),
        )


def _is_false_param(request: GenerationRequest, key: str) -> bool:
    if key not in request.params or request.params[key] is None:
        return False
    raw: object = request.params[key]
    if isinstance(raw, bool):
        return not raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("false", "0", "no", "off")
    return False


def _explicit_size_params(params: Mapping[str, object]) -> tuple[Optional[int], Optional[int]]:
    size_raw: object = params["size"] if "size" in params else None
    if isinstance(size_raw, str) and "x" in size_raw.lower():
        parts = size_raw.lower().split("x")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
    w = _opt_int(params["width"] if "width" in params else None)
    h = _opt_int(params["height"] if "height" in params else None)
    return w, h


def _opt_int(raw: object) -> Optional[int]:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


__all__ = ["Seedream5ProImageModel", "GptImageFamilyModel", "MiniMaxH3ImageModel"]
