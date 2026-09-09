"""gpt-image 家族 catalog 档位:background / output_format + 共享端口。

PortSpec 名 = params 键,不升 GenerationRequest 字段(见 skill §6.6)。
透明背景只能配 png;jpeg 会在 validate 拦截。旧值 webp 回落 png。
2.5 与 2.0 同协议,仅上游 model id 与 quality 枚举不同(2.5 多 xhigh/max)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from ..core.types import PortSpec
    from ..core.request import GenerationRequest

GPT_IMAGE2_BACKGROUNDS: tuple[str, ...] = ("transparent", "opaque", "auto")
GPT_IMAGE2_OUTPUT_FORMATS: tuple[str, ...] = ("png", "jpeg")

DEFAULT_GPT_IMAGE2_BACKGROUND: str = "auto"
DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT: str = "png"

GPT_IMAGE2_BACKGROUND_TITLES: dict[str, str] = {
    "transparent": "透明",
    "opaque": "不透明",
    "auto": "自动",
}
GPT_IMAGE2_OUTPUT_FORMAT_TITLES: dict[str, str] = {
    "png": "PNG",
    "jpeg": "JPEG",
}

_OUTPUT_FORMAT_MIME: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


def _param_str(params: Mapping[str, object] | None, key: str) -> str | None:
    if params is None or key not in params:
        return None
    raw = params[key]
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def resolve_gpt_image2_background(params: Mapping[str, object] | None) -> str:
    """缺省 / 非法回落 auto。校验阶段非法枚举由 PortSpec 拦截。"""
    raw = _param_str(params, "background")
    if raw is None:
        return DEFAULT_GPT_IMAGE2_BACKGROUND
    key = raw.lower()
    if key in GPT_IMAGE2_BACKGROUNDS:
        return key
    return DEFAULT_GPT_IMAGE2_BACKGROUND


def resolve_gpt_image2_output_format(params: Mapping[str, object] | None) -> str:
    """缺省 png;jpg → jpeg;遗留 webp → png。"""
    raw = _param_str(params, "output_format")
    if raw is None:
        return DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT
    key = raw.lower()
    if key == "jpg":
        return "jpeg"
    if key == "webp":
        return DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT
    if key in GPT_IMAGE2_OUTPUT_FORMATS:
        return key
    return DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT


def mime_for_output_format(output_format: str) -> str:
    key = output_format.lower()
    if key == "jpg":
        key = "jpeg"
    if key in _OUTPUT_FORMAT_MIME:
        return _OUTPUT_FORMAT_MIME[key]
    return _OUTPUT_FORMAT_MIME[DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT]


def gpt_image2_transparent_jpeg(params: Mapping[str, object] | None) -> bool:
    """OpenAI:transparent 背景不能配 jpeg。"""
    return resolve_gpt_image2_background(params) == "transparent" and resolve_gpt_image2_output_format(params) == "jpeg"


# catalog name = 上游 Images API 的 model 字段。2.5 与 2.0 同协议。
GPT_IMAGE_FAMILY_NAMES: tuple[str, ...] = (
    "gpt-image-2",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
)
DEFAULT_GPT_IMAGE_FAMILY_MODEL: str = "gpt-image-2"

# 2.0 仅三档;2.5 官方另有 xhigh / max(不暴露 auto,估价需要显式档)。
GPT_IMAGE2_QUALITIES: tuple[str, ...] = ("low", "medium", "high")
GPT_IMAGE25_QUALITIES: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
GPT_IMAGE_WIRE_QUALITIES: frozenset[str] = frozenset(GPT_IMAGE25_QUALITIES)


def resolve_gpt_image_family_model(request: GenerationRequest) -> str:
    """上游 model:params.model → request.model → gpt-image-2。"""
    params = request.params
    raw: object = None
    if "model" in params:
        raw = params["model"]
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    catalog = request.model
    if isinstance(catalog, str) and catalog.strip():
        return catalog.strip()
    return DEFAULT_GPT_IMAGE_FAMILY_MODEL


def gpt_image_family_inputs(
    *,
    qualities: Sequence[str] = GPT_IMAGE2_QUALITIES,
) -> dict[str, PortSpec]:
    """家族共用端口;qualities 区分 2.0 三档与 2.5 五档。"""
    from ..core.types import PortSpec, PortType
    from .gpt_image2_billing import ratio_enum_values

    quality_values = list(qualities)
    return {
        "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述"),
        "images": PortSpec(
            type=PortType.LIST,
            item_type=PortType.IMAGE,
            title="参考图片",
            description="参考图片,可选。上传即自动进入图生图/编辑模式,留空即为文生图",
        ),
        "ratio": PortSpec(
            type=PortType.ENUM,
            default="auto",
            values=ratio_enum_values(),
            title="宽高比",
            description="输出宽高比,与分辨率组合映射为 size 参数",
        ),
        "image_size": PortSpec(
            type=PortType.ENUM,
            default="2K",
            values=["1K", "2K", "4K"],
            title="分辨率",
            description="输出分辨率档位,与宽高比组合映射为 size 参数",
        ),
        "quality": PortSpec(
            type=PortType.ENUM,
            default="medium",
            values=quality_values,
            title="生成质量",
            description="生成质量档位",
        ),
        "background": PortSpec(
            type=PortType.ENUM,
            default=DEFAULT_GPT_IMAGE2_BACKGROUND,
            values=list(GPT_IMAGE2_BACKGROUNDS),
            title="背景",
            description="输出背景:transparent 透明 / opaque 不透明 / auto 由模型决定",
            value_titles=dict(GPT_IMAGE2_BACKGROUND_TITLES),
        ),
        "output_format": PortSpec(
            type=PortType.ENUM,
            default=DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT,
            values=list(GPT_IMAGE2_OUTPUT_FORMATS),
            title="输出格式",
            description="产物编码:png / jpeg。透明背景不能使用 jpeg",
            value_titles=dict(GPT_IMAGE2_OUTPUT_FORMAT_TITLES),
        ),
    }
