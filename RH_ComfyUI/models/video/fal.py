"""FLUX 3 Max / MiniMax H3 Max(fal)。

FLUX 3 Max:文生 / 图生 / 关键帧三个端点归一个模型,task_mode 选模式。
MiniMax H3 Max / Turbo:文生 / 图生 / 参考三个端点归一个模型,task_mode 选模式;
reference 模式支持图/视频/音频参考(h3max 另支持首/中/尾关键帧位)。
"""

from __future__ import annotations

from ..bridge import VideoPipelineModel
from ...core.base.video import VideoTaskShape
from ...core.base.errors import ValidationError
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...utils.backends.fal.billing import (
    RATE_PER_SECOND,
    video_usd,
    h3_video_usd,
    usd_to_points,
    h3_point_bounds,
    h3_ref_tokens_usd,
    video_point_bounds,
)
from ...utils.backends.fal.catalog import (
    RATIOS,
    H3_RATIOS,
    TASK_MODES,
    RESOLUTIONS,
    MAX_DURATION,
    MIN_DURATION,
    H3_TASK_MODES,
    MAX_KEYFRAMES,
    H3_FRAME_MODES,
    H3_RESOLUTIONS,
    H3_MAX_DURATION,
    H3_MIN_DURATION,
    FAL_VIDEO_MODELS,
    REASONING_LEVELS,
    H3_EXPANSION_MODES,
    H3_MAX_REFERENCE_FILES,
    H3_MAX_REFERENCE_AUDIOS,
    H3_MAX_REFERENCE_IMAGES,
    H3_MAX_REFERENCE_VIDEOS,
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


class _H3VideoBase(VideoPipelineModel):
    """MiniMax H3 Max / Turbo 共享参数面;turbo 参考端点无关键帧位、文图生半价。"""

    MODEL_NAME = ""
    TURBO = False

    def __init__(self) -> None:
        super().__init__(self.node_def())
        # H3 输出恒含原生音频,无关闭开关;generate_audio=False 在 prepare 里报错
        self.supports_generate_audio = True
        self.max_reference_total = H3_MAX_REFERENCE_FILES

    @classmethod
    def node_def(cls) -> NodeDef:
        turbo = cls.TURBO
        inputs: dict[str, PortSpec] = {
            "prompt": PortSpec(
                type=PortType.TEXT,
                required=True,
                title="提示词",
                description=(
                    "视频描述;参考模式用 Image 1/Video 1/Audio 1 等代号引用素材;关键帧模式下描述整体运动与镜头"
                ),
            ),
            "images": PortSpec(
                type=PortType.LIST,
                item_type=PortType.IMAGE,
                min_items=0,
                max_items=H3_MAX_REFERENCE_IMAGES + 3,
                title="参考图片",
                description=(
                    "0 张文生;1 张首帧 / 2 张首尾帧走图生;≥3 张或配了参考视频/音频走参考模式。"
                    f"参考图最多 {H3_MAX_REFERENCE_IMAGES} 张;关键帧位见 frame_mode(仅 h3max)。"
                ),
            ),
            "video_refs": PortSpec(
                type=PortType.LIST,
                item_type=PortType.VIDEO,
                max_items=H3_MAX_REFERENCE_VIDEOS,
                title="参考视频",
                description=("运动参考视频(每段 2~15s、合计 ≤15s),prompt 用 Video 1/2/... 引用;仅参考模式"),
            ),
            "audio_refs": PortSpec(
                type=PortType.LIST,
                item_type=PortType.AUDIO,
                max_items=H3_MAX_REFERENCE_AUDIOS,
                title="参考音频",
                description="参考音频(每段 2~15s、合计 ≤15s),prompt 用 Audio 1/2/... 引用;仅参考模式",
            ),
            "task_mode": PortSpec(
                type=PortType.ENUM,
                default="auto",
                values=list(H3_TASK_MODES),
                title="生成模式",
                description=(
                    "auto:按输入分流(0 图文生 / 1~2 图图生 / ≥3 图或带音视频参考走参考模式)。\n"
                    "text_to_video / image_to_video / reference_to_video 为显式锁定,输入不符会本地报错。"
                ),
            ),
            "frame_mode": PortSpec(
                type=PortType.ENUM,
                default="auto",
                values=list(H3_FRAME_MODES),
                title="图片角色",
                description=(
                    "参考模式内图片的角色(auto/reference=全当参考图):\n"
                    "first_last=图1首帧、图2尾帧、其余参考;last_only=图1尾帧;\n"
                    "first_middle_last=图1/2/3 依次作首/中/尾帧(需配 middle_frame_time,仅 h3max)。\n"
                    "i2v 模式下 auto/first_last 同义(图1首帧、图2尾帧),last_only=仅尾帧。"
                ),
            ),
            "middle_frame_time": PortSpec(
                type=PortType.NUMBER,
                title="中间帧时刻",
                description="中间帧出现的秒数(0~时长之间);仅 h3max 参考模式 + 中间帧图,缺省取时长一半",
            ),
            "ratio": PortSpec(
                type=PortType.ENUM,
                default="auto",
                values=list(H3_RATIOS),
                title="宽高比",
                description="auto=上游默认(图生跟随首帧,参考模式自适应);adaptive 仅参考模式可选",
            ),
            "resolution": PortSpec(
                type=PortType.ENUM,
                default="768p",
                values=list(H3_RESOLUTIONS),
                title="分辨率",
                description="480p/768p 原生生成;1080p 为 768p 潜空间精修",
            ),
            "duration": PortSpec(
                type=PortType.NUMBER,
                default=5,
                minimum=H3_MIN_DURATION,
                maximum=H3_MAX_DURATION,
                title="时长",
                description="秒,可小数(24fps 粒度);配 target_audio_url 时至少 2 秒",
            ),
            "prompt_expansion": PortSpec(
                type=PortType.ENUM,
                default="balanced",
                values=list(H3_EXPANSION_MODES),
                title="提示词改写",
                description="上游提示词扩写档位:disabled 不改写 / balanced ≈1s / quality 至多 ~30s",
            ),
            "target_audio_url": PortSpec(
                type=PortType.TEXT,
                title="钉住音轨",
                description="音频 URL 或 data URI(≥2s、≤15MB),替换输出音轨;与参考音频互斥",
            ),
            "safety_checker": PortSpec(
                type=PortType.BOOLEAN,
                default=True,
                title="安全审查",
                description="上游内容安全审查(默认开)",
            ),
            "seed": PortSpec(
                type=PortType.INTEGER,
                title="随机种子",
                description="随机种子,留空则随机",
            ),
        }
        return NodeDef(
            name=cls.MODEL_NAME,
            display_name=dict(FAL_VIDEO_MODELS)[cls.MODEL_NAME],
            task_type=TaskType.VIDEO,
            backend="fal",
            backend_model="minimax/h3-max-turbo" if turbo else "minimax/h3-max",
            point_cost=usd_to_points(h3_video_usd(cls.MODEL_NAME, "text_to_video", "480p", H3_MIN_DURATION)),
            description=(
                f"fal {'MiniMax H3 Max Turbo' if turbo else 'MiniMax H3 Max'}:"
                "文生 / 首尾帧图生 / 参考生视频,0.92~15 秒,480p/768p/1080p,恒含原生音频"
            ),
            knowledge_content=(
                "优势:fal 最快的视频模型(约 1s/1s),自带同步音频,支持多模态参考。\n"
                "适用场景:高频低价文生/图生;最多 9 图 + 3 视频 + 3 音频(共 ≤12 文件)的参考生成;"
                "h3max 另支持首/中/尾关键帧位(middle_frame_time)。\n"
                "计费:刊例价按秒 — "
                + ("Turbo 480p 2.5、768p 4、1080p 8 积分/秒;" if turbo else "480p 5、768p 8、1080p 16 积分/秒;")
                + "参考模式两家同按 5/8/16 档,另含 4096 免费 reference token,超量按实报 token 结算补差。\n"
                "关键帧:frame_mode + middle_frame_time(h3max 参考模式专属,turbo 无)。\n"
                "不适用场景:需要无声视频(输出恒含音频)、视频编辑/延长、帧率自定义。"
            ),
            requirements=["FAL_API_Key"],
            mode="programmatic",
            inputs=inputs,
            outputs={"video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4,恒含音频)")},
            capabilities=CapabilityManifest(supported_tasks=["video"], mode="async_poll", priority=53),
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        prepare(self.name, request)

    def estimate_cost(self, request: GenerationRequest) -> int:
        """按传参精确计价;reference 的 token 超量按估算计,成功后 settle 按实报替换。"""
        return usd_to_points(prepare(self.name, request, for_estimate=True).usd)

    def settle_cost(self, request: GenerationRequest, usage: dict[str, object]) -> int | None:
        """reference 模式按上游实报 reference_tokens 替换估算量,只补/退差额。"""
        try:
            submit = prepare(self.name, request, for_estimate=True)
        except ValidationError:
            return None
        usd = submit.usd
        if submit.mode == "reference_to_video" and "reference_tokens" in usage:
            tokens = usage["reference_tokens"]
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                usd = usd - h3_ref_tokens_usd(self.name, submit.ref_tokens_est) + h3_ref_tokens_usd(self.name, tokens)
        return usd_to_points(usd)

    def point_range(self) -> tuple[int, int]:
        return h3_point_bounds(self.name)

    def channel_bindings(self) -> list[ChannelBinding]:
        return bindings_for(self.name)

    async def unavailable_reason(self) -> str:
        for binding in self.channel_bindings():
            if await binding.channel.check_available():
                continue
            return await binding.channel.unavailable_reason()
        return f"{self.display_name} 暂不可用"


class H3MaxVideo(_H3VideoBase):
    """MiniMax H3 Max:t2v / i2v / reference(含首中尾关键帧位)。"""

    MODEL_NAME = "h3max"
    TURBO = False


class H3MaxTurboVideo(_H3VideoBase):
    """MiniMax H3 Max Turbo:更快更便宜;reference 端点无关键帧位。"""

    MODEL_NAME = "h3maxturbo"
    TURBO = True


FAL_VIDEO_MODEL_CLASSES = [Flux3MaxVideo, H3MaxVideo, H3MaxTurboVideo]

__all__ = ["FAL_VIDEO_MODEL_CLASSES", "Flux3MaxVideo", "H3MaxVideo", "H3MaxTurboVideo"]
