"""可灵图片。四行菜单各一个模型。通用是扩图和主体补全,不是文生图。"""

from __future__ import annotations

from typing import ClassVar

from ..bridge import ImagePipelineModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...utils.backends.kling.billing import cny_to_points, image_point_bounds
from ...utils.backends.kling.catalog import IMAGE_RATIOS, IMAGE_RATIOS_WITH_AUTO
from ...utils.backends.kling.channel import bindings_for
from ...utils.backends.kling.payload import prepare


class _KlingImage(ImagePipelineModel):
    _name: ClassVar[str] = ""
    _display: ClassVar[str] = ""
    _description: ClassVar[str] = ""
    _knowledge: ClassVar[str] = ""
    _ratios: ClassVar[list[str]] = list(IMAGE_RATIOS)
    _sizes: ClassVar[list[str]] = ["1K", "2K"]
    _max_images: ClassVar[int] = 1
    _point_cost: ClassVar[int] = 3
    _priority: ClassVar[int] = 46
    _prompt_required: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @classmethod
    def node_def(cls) -> NodeDef:
        inputs: dict[str, PortSpec] = {
            "prompt": PortSpec(
                type=PortType.TEXT,
                required=cls._prompt_required,
                title="提示词",
                description="画面描述。主体补全忽略提示词。",
            ),
            "images": PortSpec(
                type=PortType.LIST,
                item_type=PortType.IMAGE,
                min_items=0,
                max_items=cls._max_images,
                title="参考图片",
                description="不传则按文生图。张数和画质会决定具体接口。",
            ),
            "ratio": PortSpec(
                type=PortType.ENUM,
                default="16:9",
                values=list(cls._ratios),
                title="宽高比",
                description="auto 只在支持的接口里表示跟随参考图。",
            ),
            "image_size": PortSpec(
                type=PortType.ENUM,
                default="1K",
                values=list(cls._sizes),
                title="画质",
                description="1K / 2K / 4K 里该模型实际支持的档位",
            ),
            "n": PortSpec(
                type=PortType.INTEGER,
                default=1,
                minimum=1,
                maximum=9,
                title="张数",
                description="1~9。组图时忽略。主体补全按次计费,不乘张数。",
            ),
            "watermark": PortSpec(
                type=PortType.BOOLEAN,
                default=False,
                title="水印",
                description="是否同时生成带水印的结果",
            ),
        }
        cls._extra_inputs(inputs)
        return NodeDef(
            name=cls._name,
            display_name=cls._display,
            task_type=TaskType.IMAGE,
            backend="kling",
            backend_model=cls._name,
            point_cost=cls._point_cost,
            description=cls._description,
            knowledge_content=cls._knowledge,
            requirements=["Kling_API_Key"],
            mode="programmatic",
            inputs=inputs,
            outputs={"image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片")},
            capabilities=CapabilityManifest(supported_tasks=["image"], mode="async_poll", priority=cls._priority),
        )

    @classmethod
    def _extra_inputs(cls, inputs: dict[str, PortSpec]) -> None:
        return None

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        prepare(self.name, request)

    def estimate_cost(self, request: GenerationRequest) -> int:
        return cny_to_points(prepare(self.name, request, for_estimate=True).yuan)

    def point_range(self) -> tuple[int, int]:
        return image_point_bounds(self.name)

    def channel_bindings(self) -> list[ChannelBinding]:
        return bindings_for(self.name)

    async def unavailable_reason(self) -> str:
        for binding in self.channel_bindings():
            if await binding.channel.check_available():
                continue
            return await binding.channel.unavailable_reason()
        return f"{self.display_name} 暂不可用"


class KlingImage30(_KlingImage):
    _name = "kling-image-3.0"
    _display = "Kling Image 3.0 & 3.0 Omni"
    _description = "单图走 3.0;多图、4K、组图或 auto 走 3.0 Omni"
    _knowledge = "文生图单张不支持 auto。组图和 4K 只在 Omni。"
    _ratios = list(IMAGE_RATIOS_WITH_AUTO)
    _sizes = ["1K", "2K", "4K"]
    _max_images = 10
    _point_cost = 3
    _priority = 49

    @classmethod
    def _extra_inputs(cls, inputs: dict[str, PortSpec]) -> None:
        inputs["result_type"] = PortSpec(
            type=PortType.ENUM,
            default="single",
            values=["single", "series"],
            title="结果",
            description="series 为组图,张数改看 series_amount。",
        )
        inputs["series_amount"] = PortSpec(
            type=PortType.INTEGER,
            default=4,
            minimum=2,
            maximum=9,
            title="组图张数",
            description="2~9。也可在参数里传 auto。",
        )


class KlingImageO1(_KlingImage):
    _name = "kling-image-o1"
    _display = "Kling Image O1"
    _description = "Omni 图片,1K/2K,不支持组图和 4K"
    _knowledge = "始终走 omni-image,模型名 kling-image-o1。"
    _ratios = list(IMAGE_RATIOS_WITH_AUTO)
    _sizes = ["1K", "2K"]
    _max_images = 10
    _point_cost = 3
    _priority = 48


class KlingImageCommon(_KlingImage):
    _name = "kling-image-common"
    _display = "通用"
    _description = "扩图或主体补全,需要 1 张图"
    _knowledge = "task_mode=expand 为扩图,subject 为主体补全。扩图新增面积不能超过原图 3 倍。"
    _sizes = ["1K"]
    _max_images = 1
    _point_cost = 3
    _prompt_required = False
    _priority = 47

    @classmethod
    def _extra_inputs(cls, inputs: dict[str, PortSpec]) -> None:
        inputs["task_mode"] = PortSpec(
            type=PortType.ENUM,
            default="expand",
            values=["expand", "subject"],
            value_titles={"expand": "扩图", "subject": "主体补全"},
            title="任务",
            description="expand 扩图,subject 智能补全主体图。",
        )
        for key, title in (
            ("up_expansion_ratio", "向上扩展"),
            ("down_expansion_ratio", "向下扩展"),
            ("left_expansion_ratio", "向左扩展"),
            ("right_expansion_ratio", "向右扩展"),
        ):
            inputs[key] = PortSpec(
                type=PortType.NUMBER,
                default=0.1,
                minimum=0,
                maximum=2,
                title=title,
                description="相对原图的扩展比例,0~2。四边合计新增面积不能超过 3 倍。",
            )


class KlingImage21(_KlingImage):
    _name = "kling-image-2.1"
    _display = "Kling Image 2.1"
    _description = "文生、单图参考,或 2~4 张主体图"
    _knowledge = "一张图可选用 subject/face 参考。两张及以上走多图参考,最多 4 张。"
    _sizes = ["1K", "2K"]
    _max_images = 4
    _point_cost = 2
    _priority = 46

    @classmethod
    def _extra_inputs(cls, inputs: dict[str, PortSpec]) -> None:
        inputs["image_reference"] = PortSpec(
            type=PortType.ENUM,
            default="",
            values=["", "subject", "face"],
            value_titles={"": "不参考", "subject": "角色特征", "face": "人物长相"},
            title="参考类型",
            description="仅单图。face 要求图中只有一张脸。多图时不要填。",
        )


KLING_IMAGE_MODELS = [
    KlingImage30,
    KlingImageO1,
    KlingImageCommon,
    KlingImage21,
]

__all__ = [
    "KLING_IMAGE_MODELS",
    "KlingImage21",
    "KlingImage30",
    "KlingImageCommon",
    "KlingImageO1",
]
