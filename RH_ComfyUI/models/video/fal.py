"""FLUX 3 Max(fal)。文生 / 图生 / 关键帧三个端点归一个模型,task_mode 选模式。"""

from __future__ import annotations

from ..bridge import VideoPipelineModel
from ...core.base.video import VideoTaskShape
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...utils.backends.fal.billing import RATE_PER_SECOND, video_usd, usd_to_points, video_point_bounds
from ...utils.backends.fal.catalog import (
    RATIOS,
    TASK_MODES,
    RESOLUTIONS,
    MAX_DURATION,
    MIN_DURATION,
    MAX_KEYFRAMES,
    FAL_VIDEO_MODELS,
    REASONING_LEVELS,
)
from ...utils.backends.fal.channel import bindings_for
from ...utils.backends.fal.payload import prepare

_NAME = "flux3max"


def _estimate_resolution(request: GenerationRequest) -> str:
    """双轨读 resolution:params 优先,顶层兜底;非法值回落最低价档。"""
    raw: object = None
    if isinstance(request.params, dict) and "resolution" in request.params:
        raw = request.params["resolution"]
    if (not isinstance(raw, str) or not raw.strip()) and isinstance(request.resolution, str):
        raw = request.resolution
    res = raw.strip().lower() if isinstance(raw, str) else ""
    return res if res in RATE_PER_SECOND else RESOLUTIONS[0]


def _estimate_seconds(request: GenerationRequest) -> int:
    """双轨读 duration;非法值回落下限,估算是预演不卡用户。"""
    raw: object = request.duration
    if raw is None:
        params = request.params if isinstance(request.params, dict) else {}
        raw = params["duration"] if "duration" in params else MIN_DURATION
    seconds = MIN_DURATION
    if isinstance(raw, bool):
        pass
    elif isinstance(raw, int):
        seconds = raw
    elif isinstance(raw, float):
        seconds = int(raw)
    elif isinstance(raw, str) and raw.strip().isdigit():
        seconds = int(raw.strip())
    return max(MIN_DURATION, min(MAX_DURATION, seconds))


class Flux3MaxVideo(VideoPipelineModel):
    """fal FLUX 3 Max:三端点合一,按 task_mode / 输入自动分流。"""

    def __init__(self) -> None:
        super().__init__(self.node_def())
        # 请求默认 generate_audio=True。端口打开后基类才不会误拒。
        self.supports_generate_audio = True
        # ≥3 张图走关键帧,shape_of 判 MULTIMODAL;素材类型约束由 prepare() 细分
        self.supported_shapes = self.supported_shapes | {VideoTaskShape.MULTIMODAL}

    @staticmethod
    def node_def() -> NodeDef:
        inputs: dict[str, PortSpec] = {
            "prompt": PortSpec(
                type=PortType.TEXT,
                required=True,
                title="提示词",
                description="视频描述;关键帧模式下描述整体运动与镜头",
            ),
            "images": PortSpec(
                type=PortType.LIST,
                item_type=PortType.IMAGE,
                min_items=0,
                max_items=MAX_KEYFRAMES,
                title="参考图片",
                description=(
                    "0 张文生;1 张首帧 / 2 张首尾帧走图生;3 张以上或配了 frame_indices 走关键帧(最多 10 张)。"
                ),
            ),
            "task_mode": PortSpec(
                type=PortType.ENUM,
                default="auto",
                values=list(TASK_MODES),
                title="生成模式",
                description=(
                    "auto:按图片自动分流(0 图文生 / 1~2 图图生 / ≥3 图或带 frame_indices 关键帧)。\n"
                    "text_to_video / image_to_video / keyframes_to_video 为显式锁定,输入不符会本地报错。"
                ),
            ),
            "frame_indices": PortSpec(
                type=PortType.LIST,
                item_type=PortType.INTEGER,
                max_items=MAX_KEYFRAMES,
                title="关键帧位置",
                description=(
                    "与参考图片一一对应的帧号(24fps),唯一且 0~时长×24。"
                    "仅在关键帧模式生效;缺省时首帧 0、末帧 时长×24 均分。"
                ),
            ),
            "ratio": PortSpec(
                type=PortType.ENUM,
                default="auto",
                values=list(RATIOS),
                title="宽高比",
                description="视频宽高比;auto 文生由提示词决定、图生跟随首帧",
            ),
            "resolution": PortSpec(
                type=PortType.ENUM,
                default=RESOLUTIONS[0],
                values=list(RESOLUTIONS),
                title="分辨率",
                description="输出清晰度;上游有 2K/4K 但刊例价未公布,暂不上架",
            ),
            "duration": PortSpec(
                type=PortType.INTEGER,
                default=5,
                minimum=MIN_DURATION,
                maximum=MAX_DURATION,
                title="时长",
                description=f"秒,整数 {MIN_DURATION}~{MAX_DURATION};按秒计费",
            ),
            "reasoning_level": PortSpec(
                type=PortType.ENUM,
                default="high",
                values=list(REASONING_LEVELS),
                title="推理档位",
                description="high 画质更好更慢;low 更快。不影响积分",
            ),
            "generate_audio": PortSpec(
                type=PortType.BOOLEAN,
                default=True,
                title="有声",
                description="是否生成同步音频(官方默认开)",
            ),
            "seed": PortSpec(
                type=PortType.INTEGER,
                title="随机种子",
                description="随机种子,留空则随机",
            ),
        }
        return NodeDef(
            name=_NAME,
            display_name=FAL_VIDEO_MODELS[0][1],
            task_type=TaskType.VIDEO,
            backend="fal",
            backend_model="blackforestlabs/flux-3-max",
            point_cost=usd_to_points(video_usd("720p", MIN_DURATION)),
            description="fal FLUX 3 Max:文生 / 首尾帧图生 / 关键帧视频,5~20 秒,720p/1080p,可选有声",
            knowledge_content=(
                "优势:顶级画质(图生并列第一)、生成极快、可选同步音频、支持关键帧定位。\n"
                "适用场景:高质量文生视频、首帧/首尾帧图生视频、最多 10 个关键帧的控制生成。\n"
                "计费:按生成秒数,720p 17 积分/秒、1080p 29 积分/秒;时长 5~20 整数秒。\n"
                "关键帧:images + frame_indices(24fps 帧号,0~时长×24,唯一);缺省自动均分。\n"
                "不适用场景:需要 2K/4K、视频编辑/延长、参考视频/音频输入。"
            ),
            requirements=["FAL_API_Key"],
            mode="programmatic",
            inputs=inputs,
            outputs={"video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4)")},
            capabilities=CapabilityManifest(supported_tasks=["video"], mode="async_poll", priority=54),
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        prepare(self.name, request)

    def estimate_cost(self, request: GenerationRequest) -> int:
        """按传参精确计价:刊例价 $0.17/s(720p)、$0.29/s(1080p) × 秒数。"""
        resolution = _estimate_resolution(request)
        seconds = _estimate_seconds(request)
        return usd_to_points(video_usd(resolution, seconds))

    def point_range(self) -> tuple[int, int]:
        return video_point_bounds()

    def channel_bindings(self) -> list[ChannelBinding]:
        return bindings_for(self.name)

    async def unavailable_reason(self) -> str:
        for binding in self.channel_bindings():
            if await binding.channel.check_available():
                continue
            return await binding.channel.unavailable_reason()
        return f"{self.display_name} 暂不可用"


FAL_VIDEO_MODEL_CLASSES = [Flux3MaxVideo]

__all__ = ["FAL_VIDEO_MODEL_CLASSES", "Flux3MaxVideo"]
