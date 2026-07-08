"""视频模态的编程式模型类 — Seedance 2.0 与 Wan 2.2(蓝图 §5.2 范本)

两个参数面严重不一致的模型在同一 ABC 下共存:
- Seedance 2.0:多参考(≤12 素材)/首尾帧/单图/纯文本;480p~4k;有声开关
- Wan 2.2:仅首尾帧/首帧/纯文本;≤720p(像素积约束);任意宽高比;无有声

执行链仍复用桥接层(NodeDef + Adapter),差异全部体现在能力声明与
validate() 的跨字段约束里 —— 这正是"参数处理回到代码"的落点。
"""

from __future__ import annotations

from typing import Optional

from ..bridge import VideoPipelineModel
from ...core.base.video import VideoTaskShape
from ...core.base.errors import ValidationError
from ...core.schema.card import ModelCard
from ...core.schema.types import NodeOutput, ProgressCallback
from ...core.schema.request import GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...core.channels.registry import channel_registry
from ...utils.backends.seedance.channel import builtin_seedance_channels


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

    # ── 多供应商通道(交给通用 LoadBalancer 排序 / 熔断 / 故障切换) ──

    def _vendor_model_for(self, provider_name: str) -> Optional[str]:
        """该供应商应使用的 vendor model ID(ark 兜底 backend_model;端点即模型的供应商返回 None)。"""
        node = self.node
        vm = (node.backend_models or {}).get(provider_name) or None
        if not vm and provider_name == "ark":
            vm = node.backend_model
        return vm

    def _serves(self, provider_name: str, channel) -> bool:
        """内置供应商是否参与本节点的分发。

        - 需要 model 字段的供应商(ark):必须解析出非空 vendor model id;
        - 端点即模型的供应商(runninghub):必须在 node.backend_models 中挂名
          (值可为空串),避免"仅网关可用"的模型被错误分发到它头上。
        """
        if channel.accepts_model_field:
            return bool(self._vendor_model_for(provider_name))
        return provider_name in (self.node.backend_models or {})

    def channel_bindings(self) -> list[ChannelBinding]:
        node = self.node
        builtins = builtin_seedance_channels()
        external = channel_registry.bindings_for(node.name)

        # 节点级固定供应商 → 只走该供应商的通道
        if node.provider:
            ch = builtins.get(node.provider)
            if ch is not None:
                return [ChannelBinding(ch, vendor_model=self._vendor_model_for(node.provider))]
            return [b for b in external if b.channel.name == node.provider]

        bindings: list[ChannelBinding] = []
        for name, ch in builtins.items():
            if self._serves(name, ch):
                bindings.append(ChannelBinding(ch, vendor_model=self._vendor_model_for(name)))
        bindings.extend(external)
        return bindings

    async def execute_on_channel(
        self,
        request: GenerationRequest,
        binding: ChannelBinding,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        return await binding.channel.invoke(
            request=request,
            node=self.node,
            on_progress=on_progress,
            vendor_model=binding.vendor_model,
        )

    async def unavailable_reason(self) -> str:
        return (
            f"{self.display_name} 无可用供应商:请在 Web 控制台配置 "
            "Seedance_apikey_ark / _runninghub(或安装并配置外部供应商插件)"
        )


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
