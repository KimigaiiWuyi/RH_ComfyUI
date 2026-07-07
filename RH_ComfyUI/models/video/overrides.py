"""视频模态的编程式模型类 — Seedance 2.0 与 Wan 2.2(蓝图 §5.2 范本)

两个参数面严重不一致的模型在同一 ABC 下共存:
- Seedance 2.0:多参考(≤12 素材)/首尾帧/单图/纯文本;480p~4k;有声开关
- Wan 2.2:仅首尾帧/首帧/纯文本;≤720p(像素积约束);任意宽高比;无有声

执行链仍复用桥接层(NodeDef + Adapter),差异全部体现在能力声明与
validate() 的跨字段约束里 —— 这正是"参数处理回到代码"的落点。
"""

from __future__ import annotations

from ..bridge import VideoPipelineModel
from ...core.base.video import VideoTaskShape
from ...core.base.errors import ValidationError
from ...core.schema.card import ModelCard
from ...core.schema.request import GenerationRequest
from ...utils.core.pipeline import NodeDef


class SeedanceVideoModel(VideoPipelineModel):
    """Seedance 2.0:多参考(图+视频+音频 合计≤12)/首尾帧/单图/纯文本"""

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.FIRST_LAST_FRAME,
            VideoTaskShape.MULTIMODAL,
        }
        self.supports_generate_audio = True
        self.max_reference_total = 12
        self.card = ModelCard(
            description=node.description or "字节跳动 Seedance 2.0 统一视频生成,按输入自动切换全部形态",
            strengths=["中文语义理解强", "动作流畅", "多模态参考(图/视频/音频)", "可生成有声视频"],
            categories=["短视频", "写实", "多素材合成", "音画同步"],
            weaknesses=["高分辨率(4K)耗时较长且积分消耗高"],
            sample_prompts=["图片1为主角,音频1为背景音乐,他在草原上奔跑"],
            languages=["zh", "en"],
            speed_hint="slow",
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        if len(request.video_refs) > 3:
            raise ValidationError(f"{self.display_name} 最多接受 3 段参考视频,当前 {len(request.video_refs)} 段")
        if len(request.audio_refs) > 3:
            raise ValidationError(f"{self.display_name} 最多接受 3 段参考音频,当前 {len(request.audio_refs)} 段")


class Wan22VideoModel(VideoPipelineModel):
    """Wan 2.2:仅支持 首帧 + 尾帧 两类图,无音视频参考,无有声,≤720p(像素积)

    2026-07 收紧:Wan 2.2 仅消费首/尾帧,不再假装支持 0~9 张参考图。
    输入图片上限 = 2(首帧 + 尾帧);超过 2 张请改走 Seedance 2.0 系列。
    """

    MAX_PIXELS = 1280 * 720
    MAX_IMAGES = 2  # 首帧 + 尾帧(尾帧可缺省)

    def __init__(self, node: NodeDef) -> None:
        super().__init__(node)
        self.supported_shapes = {
            VideoTaskShape.TEXT2VIDEO,
            VideoTaskShape.IMAGE2VIDEO,
            VideoTaskShape.FIRST_LAST_FRAME,
        }
        self.supported_ratios = []  # 空 = 任意(靠 width/height 自由指定)
        self.supports_generate_audio = False
        # Wan 2.2 至多消费首帧+尾帧两张图;多参考留给 Seedance 2.0 系列
        self.max_reference_total = self.MAX_IMAGES
        self.card = ModelCard(
            description=node.description or "Wan 2.2 本地 ComfyUI 视频生成,支持文生/图生/首尾帧",
            strengths=["本地部署零 API 费用", "中文支持良好", "宽高比可任意指定"],
            categories=["短视频", "动画", "创意视频"],
            weaknesses=[
                "仅支持首尾帧(≤2 张图),不支持多素材参考",
                "最高 720p",
                "无有声视频",
            ],
            speed_hint="normal",
        )

    def validate(self, request: GenerationRequest) -> None:
        # 音视频参考直接拒绝并给出替代建议(而非静默忽略)
        if request.video_refs or request.audio_refs:
            raise ValidationError(
                "Wan 2.2 不支持视频/音频参考,请改用 Seedance 2.0(模型名 seedance2)"
            )
        # 图片数量硬上限:超过 2 张明确拒绝并指向 Seedance(避免 schema 静默丢弃多余图)
        n_imgs = len(request.images)
        if n_imgs > self.MAX_IMAGES:
            raise ValidationError(
                f"Wan 2.2 仅支持首帧 + 尾帧(最多 {self.MAX_IMAGES} 张),"
                f"当前传入 {n_imgs} 张;≥3 张请改用 Seedance 2.0 / Seedance 2.0 Fast"
            )
        super().validate(request)
        if (request.resolution or "").lower() in ("1080p", "4k"):
            raise ValidationError(f"Wan 2.2 最高支持 720p,不支持 {request.resolution}")
        if request.width * request.height > self.MAX_PIXELS:
            raise ValidationError(
                f"Wan 2.2 最高支持 720p(约 92 万像素),当前 {request.width}x{request.height} 超限"
            )


__all__ = ["SeedanceVideoModel", "Wan22VideoModel"]
