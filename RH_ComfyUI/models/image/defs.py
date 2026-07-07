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
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="生成描述"),
                "width": PortSpec(type=PortType.INTEGER, default=720, description="图片宽度"),
                "height": PortSpec(type=PortType.INTEGER, default=1280, description="图片高度"),
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
    """Nano Banana 2 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="banana2",
            display_name="Nano Banana 2",
            task_type=TaskType("image"),
            backend="gpt-image-2",
            point_cost=2,
            description="Gemini 3.1 Flash 图像生成/编辑模型,速度快,适合快速生成和预览",
            knowledge_content=(
                "Nano Banana 2 图像生成/编辑模型(Gemini 3.1 Flash)。"
                "\n"
                "优势:生成速度非常快,支持快速迭代测试,质量稳定可控,适合批量生成;"
                "\n"
                "同时支持图片编辑(传入 1~N 张参考图时自动进入编辑模式)。"
                "\n"
                "适用场景:需要较快速度但保持较好质量的图像,精细画面,快速图片编辑。"
                "\n"
                "不适用场景:需要极高细节的专业商业图(建议用 banana_pro)。"
                "\n"
            ),
            requirements=["gpt_image2_apikey"],
            backend_model="gemini-3.1-flash-image-preview",
            mode="programmatic",
            mapper_func=_gpt_image2_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="生成描述或编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=3,
                    item_type=PortType.IMAGE,
                    description="参考图片(0~3 张):\n  - 0 张=文生图\n  - 1+ 张=图片编辑",
                ),
                "width": PortSpec(type=PortType.INTEGER, default=720, description="图片宽度(仅文生图模式用于推断比例)"),
                "height": PortSpec(
                    type=PortType.INTEGER, default=1280, description="图片高度(仅文生图模式用于推断比例)"
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
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="生成描述或编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=3,
                    item_type=PortType.IMAGE,
                    description="参考图片(0~3 张):\n  - 0 张=文生图\n  - 1+ 张=图片编辑",
                ),
                "width": PortSpec(type=PortType.INTEGER, default=720, description="图片宽度(仅文生图模式用于推断比例)"),
                "height": PortSpec(
                    type=PortType.INTEGER, default=1280, description="图片高度(仅文生图模式用于推断比例)"
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
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="生成描述"),
                "images": PortSpec(
                    type=PortType.LIST,
                    item_type=PortType.IMAGE,
                    description="参考图片(可选)。上传即自动进入图生图/编辑模式,留空即为文生图",
                ),
                "width": PortSpec(type=PortType.INTEGER, default=1024, description="图片宽度(用于推断比例)"),
                "height": PortSpec(type=PortType.INTEGER, default=1024, description="图片高度(用于推断比例)"),
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
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="生成描述"),
                "width": PortSpec(type=PortType.INTEGER, default=720, description="图片宽度(用于推断比例)"),
                "height": PortSpec(type=PortType.INTEGER, default=1280, description="图片高度(用于推断比例)"),
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
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="编辑指令"),
                "images": PortSpec(
                    type=PortType.LIST,
                    required=True,
                    min_items=1,
                    max_items=3,
                    item_type=PortType.IMAGE,
                    description="待编辑图片(1~3 张)",
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
                "prompt": PortSpec(type=PortType.TEXT, required=True, description="生成描述"),
                "width": PortSpec(
                    type=PortType.INTEGER, default=720, minimum=256, maximum=2048, description="图片宽度"
                ),
                "height": PortSpec(
                    type=PortType.INTEGER, default=1280, minimum=256, maximum=2048, description="图片高度"
                ),
                "negative_prompt": PortSpec(type=PortType.TEXT, description="反向提示词"),
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


ALL_MODELS = [AnimaDef, Banana2Def, BananaProDef, GptImage2Def, MinimaxImage01Def, Qwen2511Def, Qwen2512Def]
