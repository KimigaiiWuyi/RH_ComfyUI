"""PipelineBackedModel — NodeDef + Adapter 执行链到新 ABC 的桥接层

2026-07 起模型定义全编程式:NodeDef 由各模态 defs.py 的模型类以代码构建
(node_def()),不再来自 YAML。本模块提供:
- AdapterChannel:把旧 Adapter(utils/backends)适配为 ProviderChannel;
- 四个模态桥接基类:从 NodeDef 推导能力字段(input_schema/形态/分辨率等),
  执行走 Adapter(mapper/工作流逻辑零改动);
- 需要跨字段校验的模型以子类覆盖 validate()(见 models/*/overrides.py)。
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.base.image import ImageGenerationBase
from ..core.base.music import MusicGenerationBase
from ..core.base.video import VideoTaskShape, VideoGenerationBase
from ..core.base.errors import ChannelError, GenerationError
from ..core.base.speech import DigitalHumanSpeechBase
from ..core.schema.card import ModelCard
from ..core.schema.types import PortSpec, NodeOutput, ProgressCallback
from ..core.schema.request import TaskType, GenerationRequest
from ..utils.core.pipeline import NodeDef
from ..core.base.generation import AIGCGenerationBase
from ..core.channels.channel import ChannelBinding, ProviderChannel
from ..core.channels.registry import channel_registry


class AdapterChannel(ProviderChannel):
    """把旧 Adapter(utils/backends)适配为 ProviderChannel"""

    def __init__(self, backend_name: str) -> None:
        self.name = backend_name

    def _adapter(self) -> Any:
        from ..utils.backends import backend_registry

        return backend_registry.get(self.name)

    async def check_available(self) -> bool:
        adapter = self._adapter()
        if adapter is None:
            return False
        return await adapter.check_available()

    async def unavailable_reason(self) -> str:
        adapter = self._adapter()
        if adapter is None:
            return f"后端 {self.name} 未注册"
        return await adapter.get_unavailable_reason()

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        adapter = self._adapter()
        if adapter is None:
            raise ChannelError(f"后端 {self.name} 未注册", retryable=False)
        try:
            return await adapter.execute(
                kwargs["request"],
                kwargs["node"],
                on_progress=kwargs.get("on_progress"),
            )
        except GenerationError:
            # ChannelError/ValidationError 等已有明确语义,原样抛(含 retryable 标注)
            raise
        except Exception as e:
            # 后端/上游失败翻译成可切换通道错误,让 run() 尝试下一路供应商
            raise ChannelError(
                f"后端 {self.name} 执行失败: {e}",
                retryable=True,
                channel=self.name,
                user_message=str(e),
            ) from e


class _PipelineBackedMixin:
    """桥接公共实现:身份/元数据来自 NodeDef,执行走 AdapterChannel"""

    node: NodeDef

    def _init_from_node(self, node: NodeDef) -> None:
        self.node = node
        # 实例属性遮蔽基类属性(桥接模型按 YAML 实例化,而非按类声明)
        self.name = node.name
        self.display_name = node.display_name
        self.point_cost = node.point_cost
        self.priority = node.capabilities.priority
        self.card = ModelCard(description=node.description or node.display_name)
        self.execution_mode = node.capabilities.mode
        # 只在 NodeDef 真的声明了上限时才写实例属性:留 0 的模型继续沿用类属性,
        # 这样子类可以把 max_concurrency 定义成读配置的 property(如外部插件按面板
        # 热调供应商并发)而不会在这里被一个死值覆盖掉 —— property 无 setter,
        # 无条件赋值会直接 AttributeError。
        if node.capabilities.max_concurrency > 0:
            self.max_concurrency = node.capabilities.max_concurrency
        self._channel = AdapterChannel(node.backend)

    def input_schema(self) -> dict[str, PortSpec]:
        return dict(self.node.inputs)

    def output_schema(self) -> dict[str, PortSpec]:
        if self.node.outputs:
            return dict(self.node.outputs)
        return super().output_schema()  # type: ignore[misc]

    def channel_bindings(self) -> list[ChannelBinding]:
        # 内置直连通道 + 外部插件为本模型注入的通道(如另一家供应商)
        bindings = [ChannelBinding(channel=self._channel, vendor_model=self.node.backend_model)]
        bindings.extend(channel_registry.bindings_for(self.name))
        return bindings

    async def unavailable_reason(self) -> str:
        return await self._channel.unavailable_reason()

    async def execute_on_channel(
        self,
        request: GenerationRequest,
        binding: ChannelBinding,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        # provider 归属由通道决定;run() 落 metadata["channel"]=binding.channel.name
        return await binding.channel.invoke(
            request=request,
            node=self.node,
            on_progress=on_progress,
            vendor_model=binding.vendor_model,
        )


class ImagePipelineModel(_PipelineBackedMixin, ImageGenerationBase):
    """图片模态桥接:编辑能力从 YAML 的 images 端口推导"""

    def __init__(self, node: NodeDef) -> None:
        self._init_from_node(node)
        img_port = node.inputs.get("images") or node.inputs.get("image")
        self.supports_edit = img_port is not None
        self.max_input_images = int(img_port.max_items) if img_port is not None and img_port.max_items else 0


class VideoPipelineModel(_PipelineBackedMixin, VideoGenerationBase):
    """视频模态桥接:形态/分辨率/宽高比/有声能力从 YAML 端口推导"""

    def __init__(self, node: NodeDef) -> None:
        self._init_from_node(node)
        img_port = node.inputs.get("images")
        max_imgs = int(img_port.max_items) if img_port is not None and img_port.max_items is not None else 2
        needs_image = img_port is not None and (img_port.required or (img_port.min_items or 0) >= 1)

        shapes: set[VideoTaskShape] = set()
        if not needs_image:
            shapes.add(VideoTaskShape.TEXT2VIDEO)
        if img_port is not None and max_imgs >= 1:
            shapes.add(VideoTaskShape.IMAGE2VIDEO)
        if img_port is not None and max_imgs >= 2:
            shapes.add(VideoTaskShape.FIRST_LAST_FRAME)
        if "video_refs" in node.inputs or "audio_refs" in node.inputs or "ordered_content" in node.inputs:
            shapes.add(VideoTaskShape.MULTIMODAL)
        self.supported_shapes = shapes or {VideoTaskShape.TEXT2VIDEO}

        res_port = node.inputs.get("resolution")
        self.supported_resolutions = (
            [str(v).lower() for v in res_port.values] if res_port is not None and res_port.values else []
        )
        ratio_port = node.inputs.get("ratio")
        self.supported_ratios = list(ratio_port.values) if ratio_port is not None and ratio_port.values else []
        self.supports_generate_audio = "generate_audio" in node.inputs


class MusicPipelineModel(_PipelineBackedMixin, MusicGenerationBase):
    def __init__(self, node: NodeDef) -> None:
        self._init_from_node(node)
        self.supports_lyrics = "negative_prompt" in node.inputs


class SpeechPipelineModel(_PipelineBackedMixin, DigitalHumanSpeechBase):
    """语音模态桥接:reference_audio 是否为一等端口由 YAML 声明决定"""

    def __init__(self, node: NodeDef) -> None:
        self._init_from_node(node)
        self.supports_voice_clone = "reference_audio" in node.inputs
        self.supports_mood = "mood" in node.inputs
        voice_port = node.inputs.get("voice_id")
        self.builtin_voices = list(voice_port.values) if voice_port is not None and voice_port.values else []
        # 存量本地工作流内置默认音色,未提供参考音频不报错(行为保持)
        self.has_default_voice = True

    def input_schema(self) -> dict[str, PortSpec]:
        # YAML 端口优先;若 YAML 未声明,回落模态骨架 schema(保证参考音频端口暴露)
        return dict(self.node.inputs) if self.node.inputs else self.base_speech_schema()


_MODALITY_CLASS: dict[TaskType, type] = {
    TaskType.IMAGE: ImagePipelineModel,
    TaskType.VIDEO: VideoPipelineModel,
    TaskType.MUSIC: MusicPipelineModel,
    TaskType.SPEECH: SpeechPipelineModel,
}


def build_bridge_model(
    node: NodeDef,
    custom_classes: Optional[dict[str, type]] = None,
) -> AIGCGenerationBase:
    """为一个 NodeDef 构造模型实例;custom_classes 允许按 name 指定编程式子类"""
    if custom_classes and node.name in custom_classes:
        return custom_classes[node.name](node)
    cls = _MODALITY_CLASS[node.task_type]
    return cls(node)


__all__ = [
    "AdapterChannel",
    "ImagePipelineModel",
    "VideoPipelineModel",
    "MusicPipelineModel",
    "SpeechPipelineModel",
    "build_bridge_model",
]
