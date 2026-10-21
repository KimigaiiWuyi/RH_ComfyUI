"""千问图像。六行菜单共用 DashScope 同步接口。"""

from __future__ import annotations

from typing import Protocol

from ..bridge import ImagePipelineModel
from ...core.base.errors import ValidationError
from ...utils.core.types import PortSpec, PortType, NodeOutput, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.qwen_image import (
    call_from_request,
    qwen_image_mapper,
    estimate_output_count,
)
from ...utils.backends.dashscope.config import (
    dashscope_disabled_reason,
    is_dashscope_model_enabled,
)
from ...utils.mappers.qwen_image_billing import (
    settle_qwen_image_points,
    estimate_qwen_image_points,
)
from ...utils.mappers.qwen_image_catalog import (
    QWEN_IMAGE_SPECS,
    QWEN_IMAGE_RATIOS,
    QwenImageSpec,
    qwen_image_spec,
)
from ...utils.backends.dashscope.image_api import DashScopeImageAPI

_CN = "https://dashscope.aliyuncs.com/api/v1"
_INTL = "https://dashscope-intl.aliyuncs.com/api/v1"


def _usage_blob(usage: dict) -> dict[str, object]:
    blob: dict[str, object] = {}
    for key, value in usage.items():
        if isinstance(key, str):
            blob[key] = value
    return blob


def _knowledge(spec: QwenImageSpec) -> str:
    negative = "可以写反向提示词。" if spec.allow_negative else "没有反向提示词,不想要的内容写进提示词。"
    if spec.allow_extend_mode:
        extend = "改写方式 direct 或 agent;有参考图时不能用 agent。思考只在改写打开时生效。"
    else:
        extend = "不支持改写模式和思考开关。"
    if spec.name.startswith("qwen-image-3.0"):
        bill = "3.x 按输入图张数加输出档计费。Pro 的 2K 更贵,标准版两档相同。"
    else:
        bill = "2.x 只按成功出图张数计费,参考图不另计。"
    return (
        f"{spec.display_name}({spec.name})走 DashScope 同步接口,一次请求直接返回图片,不建异步任务。"
        "\n"
        f"参考图最多 {spec.max_images} 张。{negative}{extend}"
        "\n"
        "一次可出 1~6 张。ratio 为 auto 或不传时不发尺寸。"
        "\n"
        f"{bill}地区跟随 DashScope 根地址。"
        "\n"
        "适合中文海报、文字渲染和参考图编辑。"
        "\n"
        "不适合超过 2048² 的出图,也不提供异步任务号。"
        "\n"
    )


class QwenImageModel(ImagePipelineModel):
    """勾选名等于厂商模型 ID。已保存的旧列表不会自动启用。"""

    async def check_available(self) -> bool:
        if not is_dashscope_model_enabled(self.name):
            return False
        return await super().check_available()

    async def unavailable_reason(self) -> str:
        disabled = dashscope_disabled_reason(self.name, self.display_name)
        if disabled is not None:
            return disabled
        return await super().unavailable_reason()

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        spec = qwen_image_spec(self.name)
        if spec is None:
            raise ValidationError(f"未知千问图像模型 {self.name}")
        prepared = call_from_request(spec, request)
        # 张数可能是字符串。写回整数后,请求体不再二次解析。
        request.params["n"] = prepared.n

    def estimate_cost(self, request: GenerationRequest) -> int:
        size: object = request.params["image_size"] if "image_size" in request.params else None
        count: object = request.params["n"] if "n" in request.params else None
        return estimate_qwen_image_points(
            self.name,
            image_size=size if isinstance(size, str) else None,
            ratio=request.ratio,
            num_input_images=len(request.images),
            n=estimate_output_count(count),
        )

    def settle_cost(self, request: GenerationRequest, usage: dict) -> int | None:
        return settle_qwen_image_points(
            self.name,
            _usage_blob(usage),
            num_input_images=len(request.images),
        )

    def point_range(self) -> tuple[int, int]:
        spec = qwen_image_spec(self.name)
        inputs = spec.max_images if spec is not None else 0
        low = estimate_qwen_image_points(
            self.name,
            image_size="1K",
            ratio="1:1",
            num_input_images=0,
            n=1,
            base_url=_CN,
        )
        high = estimate_qwen_image_points(
            self.name,
            image_size="2K",
            ratio="16:9",
            num_input_images=inputs,
            n=6,
            base_url=_INTL,
        )
        return (low, high)


class _QwenImageCtor(Protocol):
    def __call__(self) -> QwenImageModel: ...


def _mapper_for(spec: QwenImageSpec):
    async def _mapper(request: GenerationRequest, api: DashScopeImageAPI) -> NodeOutput:
        return await qwen_image_mapper(request, api, spec=spec)

    return _mapper


def _build(spec: QwenImageSpec) -> _QwenImageCtor:
    mapper = _mapper_for(spec)

    class Def(QwenImageModel):
        def __init__(self) -> None:
            super().__init__(self.node_def())

        @staticmethod
        def node_def() -> NodeDef:
            inputs: dict[str, PortSpec] = {
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description="画面或编辑说明。只传一段文字。",
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    item_type=PortType.IMAGE,
                    min_items=0,
                    max_items=spec.max_images,
                    title="参考图片",
                    description=f"不传则按文生图。按顺序参考,最多 {spec.max_images} 张,单张不超过 10MB。",
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="1:1",
                    values=list(QWEN_IMAGE_RATIOS),
                    value_titles={"auto": "自动"},
                    title="宽高比",
                    description="auto 不传尺寸,由模型决定。",
                ),
                "image_size": PortSpec(
                    type=PortType.ENUM,
                    default="1K",
                    values=["1K", "2K"],
                    title="画质",
                    description="1K 面积不超过 225 万像素,2K 更大。",
                ),
                "n": PortSpec(
                    type=PortType.INTEGER,
                    default=1,
                    minimum=1,
                    maximum=6,
                    title="张数",
                    description="一次出 1~6 张。",
                ),
                "prompt_extend": PortSpec(
                    type=PortType.BOOLEAN,
                    default=True,
                    title="智能改写",
                    description="用模型扩写较短的提示词。",
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN,
                    default=False,
                    title="水印",
                    description="是否在右下角加 Qwen-Image 水印。",
                ),
                "seed": PortSpec(
                    type=PortType.INTEGER,
                    minimum=0,
                    maximum=2147483647,
                    title="种子",
                    description="0~2147483647。不填则由服务随机。",
                ),
            }
            if spec.allow_negative:
                inputs["negative_prompt"] = PortSpec(
                    type=PortType.TEXT,
                    required=False,
                    title="反向提示词",
                    description="不希望出现的内容,最长 500 字。",
                )
            if spec.allow_extend_mode:
                inputs["prompt_extend_mode"] = PortSpec(
                    type=PortType.ENUM,
                    default="direct",
                    values=["direct", "agent"],
                    value_titles={"direct": "直接增强", "agent": "智能体"},
                    title="改写方式",
                    description="agent 只用于文生图。有参考图时不能选。",
                )
                inputs["enable_thinking"] = PortSpec(
                    type=PortType.BOOLEAN,
                    default=True,
                    title="思考",
                    description="只在智能改写打开时生效。",
                )
            return NodeDef(
                name=spec.name,
                display_name=spec.display_name,
                task_type=TaskType.IMAGE,
                backend="dashscope_image",
                backend_model=spec.name,
                point_cost=spec.point_cost,
                description=spec.description,
                knowledge_content=_knowledge(spec),
                requirements=["HappyHorse_apikey_dashscope"],
                mode="programmatic",
                mapper_func=mapper,
                inputs=inputs,
                outputs={"image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片")},
                capabilities=CapabilityManifest(
                    supported_tasks=["image"],
                    mode="sync",
                    priority=spec.priority,
                ),
            )

    Def.__name__ = "QwenImage_" + spec.name.replace(".", "_").replace("-", "_")
    Def.__qualname__ = Def.__name__
    return Def


_QWEN_IMAGE_BY_NAME: dict[str, _QwenImageCtor] = {spec.name: _build(spec) for spec in QWEN_IMAGE_SPECS}
QWEN_IMAGE_MODELS = list(_QWEN_IMAGE_BY_NAME.values())

__all__ = [
    "QWEN_IMAGE_MODELS",
    "QwenImageModel",
]
