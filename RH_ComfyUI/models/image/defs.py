"""models/image/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from ..bridge import ImagePipelineModel
from .overrides import Seedream5ProImageModel
from ...core.base.errors import ValidationError
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...utils.mappers.seedream import seedream_mapper as _seedream_mapper
from ...utils.mappers.gpt_image2 import gpt_image2_mapper as _gpt_image2_mapper
from ...utils.mappers.gpt_image2_billing import ratio_enum_values as _gpt_image2_ratio_values
from ...utils.mappers.image_edit import qwen_edit_mapper as _qwen_edit_mapper
from ...utils.mappers.banana_pro_billing import estimate_banana_pro_points
from ...utils.mappers.minimax_text2image import minimax_image01_mapper as _minimax_image01_mapper
from ...utils.mappers.nanobanana1_billing import estimate_nanobanana1_points
from ...utils.mappers.nanobanana2_billing import estimate_nanobanana2_points

# ── CameraAngleDef 参数范围 — 复用 RunningHub 工作流 2080138749291356162 的合法域 ──
CAMERA_ANGLE_HORIZ_MIN: float = 0.0
CAMERA_ANGLE_HORIZ_MAX: float = 360.0
CAMERA_ANGLE_VERT_MIN: float = -30.0
CAMERA_ANGLE_VERT_MAX: float = 60.0
CAMERA_ANGLE_ZOOM_MIN: float = 0.0
CAMERA_ANGLE_ZOOM_MAX: float = 10.0
# 中性视角(0/0/5)是原图,前端应直接拒绝,避免积分白扣
CAMERA_ANGLE_NEUTRAL: tuple[float, float, float] = (0.0, 0.0, 5.0)


def is_camera_angle_neutral(
    horizontal: float | None,
    vertical: float | None,
    zoom: float | None,
) -> bool:
    """中性视角判定:与 CAMERA_ANGLE_NEUTRAL 三元组全部相等视为「原图」。
    缺省值取中性值后再比较,允许前端省略参数但仍能正确识别。"""
    h = CAMERA_ANGLE_NEUTRAL[0] if horizontal is None else float(horizontal)
    v = CAMERA_ANGLE_NEUTRAL[1] if vertical is None else float(vertical)
    z = CAMERA_ANGLE_NEUTRAL[2] if zoom is None else float(zoom)
    return h == CAMERA_ANGLE_NEUTRAL[0] and v == CAMERA_ANGLE_NEUTRAL[1] and z == CAMERA_ANGLE_NEUTRAL[2]


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


class CameraAngleDef(ImagePipelineModel):
    """RH 多角度 — RunningHub 工作流 2080138749291356162

    复用 RH App 后端(走 OpenAPI v2 nodeInfoList 协议),单张参考图 +
    三个摄像机参数(水平环绕 / 垂直俯仰 / 景别缩放) → 新视角图像。

    参数语义(与 RH 工作流节点 2 完全一致):
      - horizontal_angle (0~360):  摄像机绕主体水平方向旋转的角度,向右旋转
      - vertical_angle   (-30~60): 摄像机俯仰角度,越大越俯视
      - zoom             (0~10):   景别缩放,越大越近

    (0, 0, 5) 是中性视角(原图),validate() 会拒绝提交以免白扣积分。
    固定 5 积分/次(由 point_cost 兜底,无动态计费)。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="rh_camera_angle",
            display_name="RH 多角度",
            task_type=TaskType("image"),
            catalog_group="tool",
            backend="rh_app",
            point_cost=5,
            description="基于 RunningHub 工作流的图片摄像机多角度生成,支持水平旋转、垂直俯仰、景别缩放",
            knowledge_content=(
                "RH 多角度是基于 RunningHub 工作流 2080138749291356162 实现的图片多视角生成节点。"
                "\n"
                "优势:保持原图主体与构图,仅改变摄像机视角,适合同一场景下多角度展示、物料复用。"
                "\n"
                "参数:horizontal_angle(0~360,水平旋转,向右);vertical_angle(-30~60,俯仰,越大越俯视);zoom(0~10,越大越近)。"
                "\n"
                "中性视角(0, 0, 5) 即原图,前端会拒绝提交以免白扣积分。"
                "\n"
                "凭证:复用 RH_apikey(RunningHub 通用 key)。"
                "\n"
            ),
            requirements=["rh_apikey"],
            workflow_file="2080138749291356162",
            mode="declarative",
            mappings=[
                {
                    "source": "images.0",
                    "target": "1.image",
                    "type": "upload_image",
                    "description": "image",
                },
                {
                    "source": "params.horizontal_angle",
                    "target": "2.horizontal_angle",
                    "description": "horizontal_angle",
                },
                {
                    "source": "params.vertical_angle",
                    "target": "2.vertical_angle",
                    "description": "vertical_angle",
                },
                {
                    "source": "params.zoom",
                    "target": "2.zoom",
                    "description": "zoom",
                },
            ],
            inputs={
                "images": PortSpec(
                    type=PortType.LIST,
                    required=True,
                    min_items=1,
                    max_items=1,
                    item_type=PortType.IMAGE,
                    title="参考图",
                    description="原始图片(必传 1 张)",
                ),
                "horizontal_angle": PortSpec(
                    type=PortType.NUMBER,
                    default=CAMERA_ANGLE_NEUTRAL[0],
                    minimum=CAMERA_ANGLE_HORIZ_MIN,
                    maximum=CAMERA_ANGLE_HORIZ_MAX,
                    title="水平环绕",
                    description="摄像机绕主体水平方向旋转的角度,0~360 度,向右旋转",
                ),
                "vertical_angle": PortSpec(
                    type=PortType.NUMBER,
                    default=CAMERA_ANGLE_NEUTRAL[1],
                    minimum=CAMERA_ANGLE_VERT_MIN,
                    maximum=CAMERA_ANGLE_VERT_MAX,
                    title="垂直俯仰",
                    description="摄像机俯仰角度,-30~60 度,越大越俯视",
                ),
                "zoom": PortSpec(
                    type=PortType.NUMBER,
                    default=CAMERA_ANGLE_NEUTRAL[2],
                    minimum=CAMERA_ANGLE_ZOOM_MIN,
                    maximum=CAMERA_ANGLE_ZOOM_MAX,
                    title="景别缩放",
                    description="摄像机景别缩放,0~10,越大越近",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="新视角的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="async_poll",
                priority=55,
            ),
        )

    def validate(self, request: GenerationRequest) -> None:
        # 基类会校验图片数量(本节点 max=1)
        super().validate(request)
        params = request.params
        horizontal = params.get("horizontal_angle")
        vertical = params.get("vertical_angle")
        zoom = params.get("zoom")
        if (
            horizontal is None
            and vertical is None
            and zoom is None
        ):
            # 没传任何参数视为中性,直接拒绝避免白扣
            raise ValidationError(
                f"{self.display_name}:三参数均为中性值(0/0/5),提交后等于生成原图,请调整至少一个参数"
            )
        if horizontal is not None and not (
            CAMERA_ANGLE_HORIZ_MIN <= float(horizontal) <= CAMERA_ANGLE_HORIZ_MAX
        ):
            raise ValidationError(
                f"horizontal_angle 必须在 {CAMERA_ANGLE_HORIZ_MIN:g}~{CAMERA_ANGLE_HORIZ_MAX:g} 之间"
            )
        if vertical is not None and not (
            CAMERA_ANGLE_VERT_MIN <= float(vertical) <= CAMERA_ANGLE_VERT_MAX
        ):
            raise ValidationError(
                f"vertical_angle 必须在 {CAMERA_ANGLE_VERT_MIN:g}~{CAMERA_ANGLE_VERT_MAX:g} 之间"
            )
        if zoom is not None and not (CAMERA_ANGLE_ZOOM_MIN <= float(zoom) <= CAMERA_ANGLE_ZOOM_MAX):
            raise ValidationError(
                f"zoom 必须在 {CAMERA_ANGLE_ZOOM_MIN:g}~{CAMERA_ANGLE_ZOOM_MAX:g} 之间"
            )
        if is_camera_angle_neutral(horizontal, vertical, zoom):
            raise ValidationError(
                f"{self.display_name}:三参数均为中性值(0/0/5),提交后等于生成原图,请调整至少一个参数"
            )

    def normalize(self, request: GenerationRequest) -> GenerationRequest:
        """预处理参考图:最长边 ≤ 1080px 且宽高均为 4 的倍数(RunningHub 工作流要求)。"""
        if request.images:
            from ...utils.image_process import preprocess_for_camera_angle

            request.images = [preprocess_for_camera_angle(img) for img in request.images]
        return super().normalize(request)


class ImageMattingDef(ImagePipelineModel):
    """RH 抠图 — RunningHub AI App 2084821698574114817

    单张输入图 → 去除背景，输出透明底主体图。
    走 rh_app OpenAPI v2（nodeInfoList），凭证复用 RH_apikey。
    固定积分 point_cost（无动态计费）。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="rh_image_matting",
            display_name="RH 抠图",
            task_type=TaskType("image"),
            catalog_group="tool",
            backend="rh_app",
            point_cost=2,
            description="基于 RunningHub AI 应用的图片抠图，去除背景输出透明底主体",
            knowledge_content=(
                "RH 抠图基于 RunningHub AI App 2084821698574114817 实现主体抠图。"
                "\n"
                "优势:单图一键去背景，输出透明底 PNG，适合贴图合成、电商主图、素材提取。"
                "\n"
                "输入:必传 1 张参考图；无需提示词与其它参数。"
                "\n"
                "凭证:复用 RH_apikey（RunningHub 通用 key）。"
                "\n"
            ),
            requirements=["rh_apikey"],
            workflow_file="2084821698574114817",
            mode="declarative",
            mappings=[
                {
                    "source": "images.0",
                    "target": "29.image",
                    "type": "upload_image",
                    "description": "image",
                },
            ],
            inputs={
                "images": PortSpec(
                    type=PortType.LIST,
                    required=True,
                    min_items=1,
                    max_items=1,
                    item_type=PortType.IMAGE,
                    title="参考图",
                    description="待抠图原图(必传 1 张)",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="抠图后的透明底图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="async_poll",
                priority=55,
            ),
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if not request.images:
            raise ValidationError(f"{self.display_name}:必须提供 1 张待抠图原图")


class ImageUpscaleDef(ImagePipelineModel):
    """RH 高清放大 — RunningHub AI App 2084945150656212993

    单张输入图 → 高清放大输出。
    走 rh_app OpenAPI v2（nodeInfoList），凭证复用 RH_apikey。
    固定积分 point_cost（无动态计费）。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="rh_image_upscale",
            display_name="RH 高清放大",
            task_type=TaskType("image"),
            catalog_group="tool",
            backend="rh_app",
            point_cost=3,
            description="基于 RunningHub AI 应用的图片高清放大",
            knowledge_content=(
                "RH 高清放大基于 RunningHub AI App 2084945150656212993 实现图片超分放大。"
                "\n"
                "优势:单图一键提升清晰度与分辨率，适合模糊图修复、印刷/展示前放大。"
                "\n"
                "输入:必传 1 张参考图；无需提示词与其它参数。"
                "\n"
                "凭证:复用 RH_apikey（RunningHub 通用 key）。"
                "\n"
            ),
            requirements=["rh_apikey"],
            workflow_file="2084945150656212993",
            mode="declarative",
            mappings=[
                {
                    "source": "images.0",
                    "target": "2.image",
                    "type": "upload_image",
                    "description": "image",
                },
            ],
            inputs={
                "images": PortSpec(
                    type=PortType.LIST,
                    required=True,
                    min_items=1,
                    max_items=1,
                    item_type=PortType.IMAGE,
                    title="参考图",
                    description="待放大原图(必传 1 张)",
                ),
            },
            outputs={
                "image": PortSpec(type=PortType.OUTPUT_IMAGE, description="高清放大后的图片"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["image"],
                mode="async_poll",
                priority=55,
            ),
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if not request.images:
            raise ValidationError(f"{self.display_name}:必须提供 1 张待放大原图")


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
                    default="9:16",
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

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输出分辨率分档计费(60 美元/1M tokens)。

        image_size 缺失 → 按 2K 档(默认值)估算。
        """
        image_size = request.params.get("image_size")
        return estimate_nanobanana2_points(image_size)

    def point_range(self) -> tuple[int, int]:
        """积分范围:512 档(最小) ~ 4K 档(最大)。"""
        return (
            estimate_nanobanana2_points("512"),
            estimate_nanobanana2_points("4K"),
        )


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
                    default="9:16",
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

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:一代模型无尺寸档位,固定 1290 tokens(30 美元/1M tokens)。"""
        return estimate_nanobanana1_points()

    def point_range(self) -> tuple[int, int]:
        """积分范围:固定值(min=max)。"""
        pts = estimate_nanobanana1_points()
        return (pts, pts)


class BananaProDef(ImagePipelineModel):
    """Nano Banana Pro — 高质量 Nano Banana

    通道:
    1. 原生 Gemini(``gemini-3-pro-image-preview``),与 banana1/2 共用 Gemini_* 配置;
    2. 内置 OpenAI 兼容 gpt-image-2 适配(``nano-banana-2-2k``);
    3. 外部插件经 channel_registry 注入(如 aifoundation NBP)。

    计费独立:输入 0.0011 美元/张 + 输出 120 美元/1M tokens 按分辨率分档
    (1K~2K 同价,4K 单独一档)。point_cost 仅作未知参数时的兜底。

    ⚠️ 注意:此模型 schema 中**没有** quality 字段。官方计费曲线不区分 quality
    档位 —— 暴露会让用户在切换 quality 时看到积分不变,造成"积分 bug"误判。
    前端要切 quality 请用 gpt-image-2。
    """

    # 原生 Gemini 侧的 vendor model id(与 banana2 的 flash 代际区分)
    GEMINI_VENDOR_MODEL = "gemini-3-pro-image-preview"

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
            description="Nano Banana Pro 高质量图像生成/编辑模型(Gemini 3 Pro Image + 兼容通道)",
            knowledge_content=(
                "Nano Banana Pro 高质量图像生成/编辑模型。"
                "\n"
                "优势:图像质量非常高,细节丰富细腻,色彩表现优秀,适合专业输出;"
                "\n"
                "同时支持图片编辑(传入 1~N 张参考图时自动进入编辑模式)。"
                "\n"
                "内置供应商:Gemini 3 Pro Image(gemini-3-pro-image-preview,与 banana1/2 共用"
                "Gemini 配置)+ OpenAI 兼容 gpt-image-2 通道;外部插件可追加更多供应商。"
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
                    max_items=14,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生图,1+ 张=图片编辑",
                ),
                # 上游按 ratio + image_size → size 映射请求(与计费表同源)
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=_gpt_image2_ratio_values(),
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
                # 注:故意不暴露 quality 字段 —— 官方计费曲线不区分 quality 档位,
                # 暴露会让前端误以为切 quality 会影响积分(实际不影响)。
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

    def channel_bindings(self) -> list[ChannelBinding]:
        """Gemini Pro 优先 + 内置 gpt-image-2 适配 + 外部注入通道。"""
        from ...core.channels.registry import channel_registry
        from ...utils.backends.gemini_image.channel import GeminiImageChannel

        bindings = [
            ChannelBinding(GeminiImageChannel(), vendor_model=self.GEMINI_VENDOR_MODEL),
            ChannelBinding(channel=self._channel, vendor_model=self.node.backend_model),
        ]
        bindings.extend(channel_registry.bindings_for(self.name))
        return bindings

    async def unavailable_reason(self) -> str:
        return (
            "Nano Banana Pro 无可用供应商:配置 Gemini(Gemini_Image_apikey / Vertex) "
            "或 OpenAI 兼容生图(OpenAI_Image_apikey / 配置池)或外部供应商插件"
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:独立于 gpt-image-2,按输入图片数 + 输出分辨率分档计费。

        输入:每张 0.0011 美元;输出:120 美元/1M tokens,按 image_size 分档
        (1K~2K 同价 14 积分,4K 24 积分,按官方文档 token 数表)。
        image_size 缺失 → 按 2K 档(默认值)估算。

        ⚠️ 故意不读 quality:banana_pro 计费曲线无 quality 维度,读它会导致
        切换 quality 时积分预期变化但实际不变的"假 bug"。
        """
        image_size = request.params.get("image_size")
        num_input_images = len(request.images) if request.images else 0
        return estimate_banana_pro_points(num_input_images, image_size)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(0 输入 + 1K) ~ 最大(3 输入 + 4K)。"""
        return (
            estimate_banana_pro_points(0, "1K"),
            estimate_banana_pro_points(3, "4K"),
        )


class GptImage2Def(ImagePipelineModel):
    """GPT-Image2 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)

    动态计费:按 quality + 输出像素面积折算 tokens,210 元/1M tokens。
    point_cost 仅作未知参数时的兜底。
    """

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
                # OpenAI images API 接受 size(由 ratio + image_size 共同映射) + quality
                # ratio 枚举来自计费/像素真源表(含 1:2 / 2:1)
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=_gpt_image2_ratio_values(),
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
                    values=["low", "medium", "high"],
                    title="生成质量",
                    description="生成质量档位",
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

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按 quality + ratio + image_size 折算 tokens。

        210 元 / 1M tokens,1 元 = 100 积分。参数缺失时按 medium + 1024x1024 估算。
        """
        from ...utils.mappers.gpt_image2_billing import estimate_gpt_image2_points

        quality = request.params.get("quality")
        image_size = request.params.get("image_size")
        return estimate_gpt_image2_points(quality, request.ratio, image_size)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(low + 1K) ~ 最大(high + 4K)。"""
        from ...utils.mappers.gpt_image2_billing import estimate_gpt_image2_points

        return (
            estimate_gpt_image2_points("low", "1:1", "1K"),
            estimate_gpt_image2_points("high", "1:1", "4K"),
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
    """Qwen-Edit 2511 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)

    固定价格:15 积分/次。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="qwen_2511",
            display_name="Qwen-Edit 2511",
            task_type=TaskType("image"),
            backend="comfyui",
            point_cost=15,
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
                max_concurrency=1,  # 本地 ComfyUI 共用一块 GPU,工作流必须串行
                priority=70,
            ),
        )


class Qwen2512Def(ImagePipelineModel):
    """Qwen-Image 2512 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)

    固定价格:15 积分/次。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="qwen_2512",
            display_name="Qwen-Image 2512",
            task_type=TaskType("image"),
            backend="comfyui",
            point_cost=15,
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
                max_concurrency=1,  # 本地 ComfyUI 共用一块 GPU,工作流必须串行
                priority=80,
            ),
        )


class Seedream5Def(ImagePipelineModel):
    """Seedream 5.0 Lite — 火山方舟 Doubao Seedream 5.0 Lite 图片生成/编辑模型

    注:以下 backend_model 字段为占位符,请按 ARK 控制台开通页返回的 Model ID
    核对修改(`[开通模型服务]` 入口:console.volcengine.com/ark)。Lite 与 Pro 同属
    「Seedream 5.0 系列」,API Key 与 Base URL 直接复用 Seedance_apikey_ark /
    Seedance_BaseURL_ark(同源 ARK 平台,凭证通用)。

    固定价格:输入图免费,输出图 0.22 元 = 22 积分。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="seedream5",
            display_name="Seedream 5.0 Lite",
            task_type=TaskType("image"),
            backend="seedream",
            point_cost=22,
            description=(
                "火山方舟 Doubao Seedream 5.0 Lite 图片生成/编辑模型。"
                "支持文生图 / 单图编辑 / 多图参考(0~14 张),"
                "提供 2K/3K/4K 三档分辨率与自然语言宽高比描述。"
            ),
            knowledge_content=(
                "Seedream 5.0 Lite 是字节跳动火山方舟推出的图片生成/编辑模型(Lite 版)。"
                "\n"
                "优势:中文提示词理解强,支持 0~14 张参考图(2~10 张时效果最佳),"
                "\n"
                "支持 2K/3K/4K 分辨率档位与显式像素模式,出图速度较快。"
                "\n"
                "适用场景:高质量写实/二次元/创意图像生成,商品图,场景插画,概念设计。"
                "\n"
                "不适用场景:需要极高高分辨率或商业级精修(可改用 Pro)。"
                "\n"
                "凭证:复用 Seedance 面板的火山方舟 API Key(Seedance_apikey_ark),"
                "\n"
                "与 Seedance 视频共用同一 ARK Key,无需单独配置。"
                "\n"
                "提示词建议:中文为主,可在 prompt 末尾用「横构图/竖构图/正方形」"
                "\n"
                "等自然语言 hint 描述宽高比,模型会自动映射到 ARK 的 size 档位。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            # 占位 Model ID —— 实际开通后请按 ARK 控制台返回的 ID 修改
            backend_model="doubao-seedream-5-0-lite-250915",
            mode="programmatic",
            mapper_func=_seedream_mapper,
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description="生成描述,支持中英文。建议不超过 300 字。",
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=14,  # Lite 上限(ARK Seedream 5.0 lite/4.5/4.0 通用)
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description=(
                        "参考图片:0 张=文生图;1 张=图生图/编辑;2~14 张=多图参考。"
                        "格式 jpeg/png/webp/bmp/tiff/gif/heic/heif,单张 ≤30MB。"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description=("输出宽高比,会以自然语言方式拼到 prompt 末尾;具体像素由 ARK 按 size 档位自主选择。"),
                ),
                "size_mode": PortSpec(
                    type=PortType.ENUM,
                    default="2K",
                    values=["2K", "3K", "4K"],
                    title="分辨率档位",
                    description="Lite 支持 2K/3K/4K 三档,默认 2K。",
                ),
                "output_format": PortSpec(
                    type=PortType.ENUM,
                    default="png",
                    values=["png", "jpeg"],
                    title="输出格式",
                    description="生成图片的文件格式(PNG 透明 / JPEG 体积小)。",
                ),
                "response_format": PortSpec(
                    type=PortType.ENUM,
                    default="url",
                    values=["url", "b64_json"],
                    title="返回格式",
                    description=("url=返回 24h 有效下载链接(本节点会立即下载转 bytes);b64_json=直接返回 Base64。"),
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN,
                    default=False,
                    title="添加水印",
                    description="是否在生成图右下角添加「AI 生成」水印。",
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


class Seedream5ProDef(Seedream5ProImageModel):
    """Seedream 5.0 Pro — 火山方舟 Doubao Seedream 5.0 Pro 图片生成/编辑模型(高质量档)

    Pro 在 Lite 能力基础上:
    - 参考图上限降为 10 张(ARK Seedream 5.0 pro 上限,见 ARK 文档);
    - 分辨率档位仅 1K/2K 两档(Pro 不支持 3K/4K);
    - 显式像素模式 (size=WxH) 额外受像素积约束:[921600, 4624220]、
      宽高比约束:[1/16, 16](由 Seedream5ProImageModel.validate() 覆盖);
    - 不支持 sequential_image_generation / web_search 工具(mapper 防御性拦截)。

    注:backend_model 为占位符,请按 ARK 控制台开通页返回的 Model ID 修改。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="seedream5_pro",
            display_name="Seedream 5.0 Pro",
            task_type=TaskType("image"),
            backend="seedream",
            point_cost=4,
            description=(
                "火山方舟 Doubao Seedream 5.0 Pro 图片生成/编辑模型(高质量档)。"
                "支持文生图 / 单图编辑 / 多图参考(0~10 张),1K/2K 两档,"
                "出图质量与细节优于 Lite。"
            ),
            knowledge_content=(
                "Seedream 5.0 Pro 是字节跳动火山方舟的高质量图片生成/编辑模型(Pro 版)。"
                "\n"
                "优势:画面细节、色彩、构图明显优于 Lite,适合最终输出与商业级场景;"
                "\n"
                "支持 0~10 张参考图(单图编辑 + 多图参考)。"
                "\n"
                "适用场景:商业级人像/产品/场景图,概念精修,海报与封面图。"
                "\n"
                "不适用场景:实时预览 / 批量草图(可改用 Lite 节省积分)。"
                "\n"
                "约束:显式像素模式总像素需在 [921600, 4624220],"
                "\n"
                "宽高比需在 [1/16, 16] 范围内;"
                "\n"
                "分辨率档位仅 1K/2K,不支持 3K/4K。"
                "\n"
                "凭证:复用 Seedance 面板的火山方舟 API Key,与 Seedance 视频共用。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            # 占位 Model ID —— 实际开通后请按 ARK 控制台返回的 ID 修改
            backend_model="doubao-seedream-5-0-pro-250915",
            mode="programmatic",
            mapper_func=_seedream_mapper,
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description="生成描述,支持中英文。建议不超过 300 字。",
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=10,  # Pro 上限(文档「Seedream 5.0 pro 最多支持传入 10 张参考图」)
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description=(
                        "参考图片:0 张=文生图;1 张=图生图/编辑;2~10 张=多图参考。"
                        "格式 jpeg/png/webp/bmp/tiff/gif/heic/heif,单张 ≤30MB。"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"],
                    title="宽高比",
                    description=("输出宽高比,会以自然语言方式拼到 prompt 末尾;具体像素由 ARK 按 size 档位自主选择。"),
                ),
                "size_mode": PortSpec(
                    type=PortType.ENUM,
                    default="2K",
                    values=["1K", "2K"],  # Pro 仅支持 1K/2K
                    title="分辨率档位",
                    description="Pro 仅支持 1K/2K 两档,默认 2K。",
                ),
                "output_format": PortSpec(
                    type=PortType.ENUM,
                    default="png",
                    values=["png", "jpeg"],
                    title="输出格式",
                    description="生成图片的文件格式(PNG 透明 / JPEG 体积小)。",
                ),
                "response_format": PortSpec(
                    type=PortType.ENUM,
                    default="url",
                    values=["url", "b64_json"],
                    title="返回格式",
                    description=("url=返回 24h 有效下载链接(本节点会立即下载转 bytes);b64_json=直接返回 Base64。"),
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN,
                    default=False,
                    title="添加水印",
                    description="是否在生成图右下角添加「AI 生成」水印。",
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

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:输入图首张免费 + 第 2 张起 2 积分/张,输出图按分辨率分档。

        输出:1K (≤236 万像素) = 30 积分,2K (>236 万像素) = 60 积分。
        """
        from ...utils.mappers.seedream5_pro_billing import estimate_seedream5_pro_points

        num_input_images = len(request.images) if request.images else 0
        # 通用 estimate API 用 image_size(同 gpt-image-2 / banana_pro 一致);
        # 节点定义内部叫 size_mode,这里兼容两种 key 以防漏传。
        size_mode = request.params.get("image_size") or request.params.get("size_mode")
        return estimate_seedream5_pro_points(num_input_images, size_mode)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(0 输入 + 1K) ~ 最大(10 输入 + 2K)。"""
        from ...utils.mappers.seedream5_pro_billing import estimate_seedream5_pro_points

        return (
            estimate_seedream5_pro_points(0, "1K"),
            estimate_seedream5_pro_points(10, "2K"),
        )


ALL_MODELS = [
    AnimaDef,
    Banana1Def,
    Banana2Def,
    BananaProDef,
    CameraAngleDef,
    ImageMattingDef,
    ImageUpscaleDef,
    GptImage2Def,
    MinimaxImage01Def,
    Qwen2511Def,
    Qwen2512Def,
    Seedream5Def,
    Seedream5ProDef,
]
