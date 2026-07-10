"""models/image/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from ..bridge import ImagePipelineModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...utils.mappers.gpt_image2 import gpt_image2_mapper as _gpt_image2_mapper
from ...utils.mappers.image_edit import qwen_edit_mapper as _qwen_edit_mapper
from ...utils.mappers.minimax_text2image import minimax_image01_mapper as _minimax_image01_mapper


class AnimaDef(ImagePipelineModel):
    """Anima — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="anima",
            display_name="Anima",
            task_type=TaskType("image"),
            backend="rh_app",
            point_cost=2,
            description="专精于二次元风格图像生成的 RunningHub AI 应用，擅长动漫角色、插画、场景等",
            knowledge_content=(
                "Anima 是一款专精于二次元风格图像生成的 AI 应用。"
                "\n"
                "优势：二次元动漫风格表现力强，角色立绘质量高，色彩鲜明，细节丰富。"
                "\n"
                "适用场景：动漫角色生成、二次元插画、萌系头像、动漫场景、同人创作。"
                "\n"
                "不适用场景：写实照片、3D 渲染、非二次元风格图像。"
                "\n"
                "提示词建议：使用中文或日文描述角色特征、服装、表情、背景、画风等。"
                "\n"
            ),
            requirements=["rh_apikey"],
            workflow_file="2059263409362923521",
            mode="declarative",
            mappings=[
                {"source": "prompt", "target": "12.prompt", "description": "prompt"},
                {"source": "width", "target": "7.width", "default": "720", "description": "width"},
                {"source": "height", "target": "7.height", "default": "1280", "description": "height"},
            ],
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述"),
                "width": PortSpec(type=PortType.INTEGER, default=720, title="宽度", description="图片宽度"),
                "height": PortSpec(type=PortType.INTEGER, default=1280, title="高度", description="图片高度"),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的二次元图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="async_poll",
                priority=50,
            ),
        )


class Banana2Def(ImagePipelineModel):
    """Nano Banana 2 — 走原生 Gemini Interactions API(非 OpenAI 兼容网关)

    独立于 gpt-image-2:唯一通道是 GeminiImageChannel(填 Project ID 走 VertexAI,
    留空走 AI Studio)。请求 Nano Banana 2 不会经过 gpt-image-2 后端。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="banana2",
            display_name="Nano Banana 2",
            task_type=TaskType("image"),
            backend="gemini-image",
            point_cost=2,
            description="Gemini 3.1 Flash 图像生成/编辑模型(原生 Interactions API),速度快",
            knowledge_content=(
                "Nano Banana 2 图像生成/编辑模型(Gemini 3.1 Flash,原生 Interactions API)。"
                "\n"
                "优势:生成速度快,质量稳定,支持图片编辑(传入 1~N 张参考图自动进入编辑)。"
                "\n"
                "适用场景:较快速度但保持较好质量的图像,精细画面,快速图片编辑。"
                "\n"
                "不适用场景:需要极高细节的专业商业图(建议用 banana_pro)。"
                "\n"
                "凭证:VertexAI(填 Project ID,key 作 Bearer)或 AI Studio(仅需 key)。"
                "\n"
            ),
            requirements=["gemini_image_apikey"],
            backend_model="gemini-3.1-flash-image-preview",
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述或编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=14,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生图,1+ 张=图片编辑/多图参考",
                ),
                # Gemini 只吃 aspect_ratio + image_size,不吃宽高像素
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="1:1",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description="宽高比",
                ),
                "image_size": PortSpec(
                    type=PortType.ENUM,
                    default="2K",
                    values=["512", "1K", "2K", "4K"],
                    title="尺寸档位",
                    description="输出尺寸档位",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",
                priority=70,
            ),
        )

    def channel_bindings(self) -> list[ChannelBinding]:
        from ...core.channels.registry import channel_registry
        from ...utils.backends.gemini_image.channel import GeminiImageChannel

        bindings = [ChannelBinding(GeminiImageChannel(), vendor_model=self.node.backend_model)]
        bindings.extend(channel_registry.bindings_for(self.name))
        return bindings

    async def unavailable_reason(self) -> str:
        return "Nano Banana 2 未配置 Gemini API Key(Gemini_Image_apikey)"


class Banana1Def(ImagePipelineModel):
    """Nano Banana 1 — 一代模型(gemini-2.5-flash-image),走同一条 Gemini 通道

    与 banana2 同构:唯一内置通道是 GeminiImageChannel(AI Studio / VertexAI 双模),
    vendor model 换成一代 ID;外部插件可经 channel_registry 追加供应商。
    一代不支持 image_size 尺寸档(mapper 对 2.5 系自动不发该字段),故无此端口。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="banana1",
            display_name="Nano Banana 1",
            task_type=TaskType("image"),
            backend="gemini-image",
            point_cost=1,
            description="Gemini 2.5 Flash 图像生成/编辑模型(一代 Nano Banana),轻量快速",
            knowledge_content=(
                "Nano Banana 1 图像生成/编辑模型(Gemini 2.5 Flash,一代)。"
                "\n"
                "优势:速度快、成本低,支持图片编辑(传入 1~3 张参考图自动进入编辑)。"
                "\n"
                "适用场景:快速预览、批量测试、对细节要求不高的日常生成。"
                "\n"
                "不适用场景:高分辨率/高细节输出(无尺寸档位,建议用 banana2 / banana_pro)。"
                "\n"
                "凭证:与 banana2 共用 Gemini 配置(VertexAI 或 AI Studio)。"
                "\n"
            ),
            requirements=["gemini_image_apikey"],
            backend_model="gemini-2.5-flash-image",
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述或编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=3,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生图,1+ 张=图片编辑/多图参考",
                ),
                # 一代只吃 aspect_ratio(无 image_size 尺寸档)
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="1:1",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description="宽高比",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",
                priority=55,
            ),
        )

    def channel_bindings(self) -> list[ChannelBinding]:
        from ...core.channels.registry import channel_registry
        from ...utils.backends.gemini_image.channel import GeminiImageChannel

        bindings = [ChannelBinding(GeminiImageChannel(), vendor_model=self.node.backend_model)]
        bindings.extend(channel_registry.bindings_for(self.name))
        return bindings

    async def unavailable_reason(self) -> str:
        return "Nano Banana 1 无可用供应商:配置 Gemini(Gemini_Image_apikey)或外部供应商插件"


class BananaProDef(ImagePipelineModel):
    """Nano Banana Pro — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="banana_pro",
            display_name="Nano Banana Pro",
            task_type=TaskType("image"),
            backend="gpt-image-2",
            point_cost=3,
            description="Nano Banana 2.2K 高质量图像生成/编辑模型",
            knowledge_content=(
                "Nano Banana 2.2K 高质量图像生成/编辑模型。"
                "\n"
                "优势:图像质量非常高,细节丰富细腻,色彩表现优秀,适合专业输出;"
                "\n"
                "同时支持图片编辑(传入 1~N 张参考图时自动进入编辑模式)。"
                "\n"
                "适用场景:需要最终输出的高质量图像,专业创作场景,商业项目,精细细节的画面,精细图片编辑。"
                "\n"
                "不适用场景:快速预览、批量测试(建议用 banana2)。"
                "\n"
            ),
            requirements=["gpt_image2_apikey"],
            backend_model="nano-banana-2-2k",
            mode="programmatic",
            mapper_func=_gpt_image2_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述或编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=3,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生图,1+ 张=图片编辑",
                ),
                # 上游按 aspect_ratio→size 映射请求(GPTImage2API),不吃宽高像素
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description="输出宽高比,上游按比例映射为 size 参数",
                ),
                # NBP 档位无 512;由支持尺寸档的通道(如 AI 基座 NBP)消费,
                # 走 OpenAI 兼容网关的通道忽略该字段(size 已由 ratio 决定)
                "image_size": PortSpec(
                    type=PortType.ENUM,
                    default="2K",
                    values=["1K", "2K", "4K"],
                    title="尺寸档位",
                    description="输出尺寸档位,NBP 不支持 512",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",
                priority=60,
            ),
        )


class GptImage2Def(ImagePipelineModel):
    """GPT-Image2 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="gpt-image-2",
            display_name="GPT-Image2",
            task_type=TaskType("image"),
            backend="gpt-image-2",
            point_cost=2,
            description=(
                "OpenAI 兼容协议的 GPT-Image2 生图模型。\n"
                "一个物理模型同时支持文生图、图生图、图片编辑,根据 request.images 自适应切换。\n"
                "凭证可指向任何兼容 OpenAI 协议的网关(OneAPI / NewAPI / OpenRouter / Local Ollama 等),\n"
                "也能直接使用 OpenAI 官方接口。"
            ),
            knowledge_content=(
                "GPT-Image2 是 OpenAI 兼容协议的生图模型,具备完整的多模态生成能力。"
                "\n"
                "优势："
                "\n"
                "- 单端点覆盖文生图 / 图生图 / 图片编辑三种模式,无需切换模型或复制配置"
                "\n"
                "- 自适应输入:有图片时自动进入图生图 / 编辑模式,无图片时进入文生图模式"
                "\n"
                "- 兼容任何暴露 /v1/images/generations 的 OpenAI 兼容服务(官方 / 第三方聚合 / 本地)"
                "\n"
                "适用场景："
                "\n"
                "- 需要统一生图能力、简化配置的对话机器人与 Web 应用"
                "\n"
                "- 希望根据用户上传图片动态切换生成模式"
                "\n"
                "- 复杂的图片编辑 / 画风转变等功能"
                "\n"
                "不适用场景："
                "\n"
                "- 无"
                "\n"
            ),
            requirements=["gpt_image2_apikey"],
            mode="programmatic",
            mapper_func=_gpt_image2_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述"),
                "images": PortSpec(
                    type=PortType.LIST,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片,可选。上传即自动进入图生图/编辑模式,留空即为文生图",
                ),
                # OpenAI images API 只吃 size(由 aspect_ratio 映射),不吃宽高像素
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="1:1",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description="输出宽高比,上游按比例映射为 size 参数",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",
                priority=65,
            ),
        )


class MinimaxImage01Def(ImagePipelineModel):
    """MiniMax Image-01 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="minimax_image01",
            display_name="MiniMax Image-01",
            task_type=TaskType("image"),
            backend="minimax",
            point_cost=3,
            description="MiniMax image-01 文生图模型，支持多种宽高比，生成质量高，适合人像、场景等",
            knowledge_content=(
                "MiniMax image-01 是 MiniMax 推出的高质量文生图模型。"
                "\n"
                "优势：图像质量高，支持多种宽高比（1:1/16:9/4:3/3:2/2:3/3:4/9:16/21:9），支持 prompt 优化。"
                "\n"
                "适用场景：人像写真、场景生成、创意图像、产品图、插画等。"
                "\n"
                "不适用场景：需要精确像素控制的专业设计场景。"
                "\n"
                "提示词建议：支持中英文描述，建议详细描述主体、场景、风格、光线等要素。"
                "\n"
            ),
            requirements=["minimax_apikey"],
            mode="programmatic",
            mapper_func=_minimax_image01_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述"),
                # MiniMax image-01 实际请求参数是 aspect_ratio(mapper 不发宽高像素)
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description="宽高比,对应 MiniMax aspect_ratio 参数",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="async_poll",
                priority=65,
            ),
        )


class Qwen2511Def(ImagePipelineModel):
    """Qwen-Edit 2511 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="qwen_2511",
            display_name="Qwen-Edit 2511",
            task_type=TaskType("image"),
            backend="comfyui",
            point_cost=4,
            description="专业的图像编辑模型,支持中文指令和多图输入",
            knowledge_content=(
                "专业的图像编辑模型(Qwen-Image-Edit 2511)。"
                "\n"
                "优势:中文指令理解准确,支持精确的区域编辑,可同时处理多图输入。"
                "\n"
                "适用场景:局部修图和编辑,多图融合处理,照片精修,添加或删除元素。"
                "\n"
                "不适用场景:从零生图(建议用 qwen_2512),本节点需要至少 1 张参考图。"
                "\n"
            ),
            requirements=["comfyui_url"],
            workflow_file="qwen_edit_2511.json",
            mode="programmatic",
            mapper_func=_qwen_edit_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="编辑指令", description="编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    required=True,
                    min_items=1,
                    max_items=3,
                    item_type=PortType.IMAGE,
                    title="待编辑图片",
                    description="待编辑图片",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="编辑后的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",
                priority=70,
            ),
        )


class Qwen2512Def(ImagePipelineModel):
    """Qwen-Image 2512 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="qwen_2512",
            display_name="Qwen-Image 2512",
            task_type=TaskType("image"),
            backend="comfyui",
            point_cost=2,
            description="千问Image2512模型，擅长中文提示词理解，适合各种风格的图像生成",
            knowledge_content=(
                "千问Image2512模型，擅长中文提示词理解，适合各种风格的图像生成。"
                "\n"
                "优势：中文提示词理解能力强，支持复杂风格描述，输出质量稳定可靠，成本低。"
                "\n"
                "适用场景：中文场景的图像生成，简单需求，二次元动漫头像，通用图像生成。"
                "\n"
                "不适用场景：需要极高细节的专业商业图、超写实人像。"
                "\n"
            ),
            requirements=["comfyui_url"],
            workflow_file="qwen_2512.json",
            mode="declarative",
            mappings=[
                {"source": "prompt", "target": "108.inputs.text"},
                {"source": "width", "target": "107.inputs.width", "default": 720},
                {"source": "height", "target": "107.inputs.height", "default": 1280},
            ],
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="生成描述"),
                "width": PortSpec(
                    type=PortType.INTEGER, default=720, minimum=256, maximum=2048, title="宽度", description="图片宽度"
                ),
                "height": PortSpec(
                    type=PortType.INTEGER, default=1280, minimum=256, maximum=2048, title="高度", description="图片高度"
                ),
                "negative_prompt": PortSpec(
                    type=PortType.TEXT, title="负向提示词", description="负向提示词,不希望出现在画面中的内容"
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="生成的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="sync",
                priority=80,
            ),
        )


ALL_MODELS = [
    AnimaDef,
    Banana1Def,
    Banana2Def,
    BananaProDef,
    GptImage2Def,
    MinimaxImage01Def,
    Qwen2511Def,
    Qwen2512Def,
]
