"""可灵视频。五行菜单各一个模型,路径在 payload 里按输入拆开。"""

from __future__ import annotations

from typing import ClassVar

from ..bridge import VideoPipelineModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...core.channels.channel import ChannelBinding
from ...utils.backends.kling.billing import cny_to_points, video_point_bounds
from ...utils.backends.kling.catalog import VIDEO_RATIOS, VIDEO_RATIOS_40
from ...utils.backends.kling.channel import bindings_for
from ...utils.backends.kling.payload import prepare

_RATIOS = list(VIDEO_RATIOS)
_RATIOS_40 = list(VIDEO_RATIOS_40)


class _KlingVideo(VideoPipelineModel):
    """子类只填身份和能力。校验、计费、通道都走可灵客户端。"""

    _name: ClassVar[str] = ""
    _display: ClassVar[str] = ""
    _description: ClassVar[str] = ""
    _knowledge: ClassVar[str] = ""
    _resolutions: ClassVar[list[str]] = ["720p", "1080p"]
    _duration: ClassVar[tuple[int, int]] = (3, 15)
    _max_images: ClassVar[int] = 1
    _allow_video: ClassVar[bool] = False
    _max_videos: ClassVar[int] = 1
    _ratios: ClassVar[list[str]] = _RATIOS
    _point_cost: ClassVar[int] = 56
    _priority: ClassVar[int] = 48
    _audio_help: ClassVar[str] = "是否生成有声视频"
    _images_help: ClassVar[str] = "0 张为文生。1 张为首帧。支持尾帧的模型可以再给 1 张。"
    _video_role_help: ClassVar[str] = "feature: 特征参考,只可配首帧且无声。base: 编辑该视频,不能配首尾帧。"

    def __init__(self) -> None:
        super().__init__(self.node_def())
        # 请求默认 generate_audio=True。端口打开后基类才不会误拒。
        self.supports_generate_audio = True

    @classmethod
    def node_def(cls) -> NodeDef:
        inputs: dict[str, PortSpec] = {
            "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="视频描述"),
            "images": PortSpec(
                type=PortType.LIST,
                item_type=PortType.IMAGE,
                min_items=0,
                max_items=cls._max_images,
                title="参考图片",
                description=cls._images_help,
            ),
            "ratio": PortSpec(
                type=PortType.ENUM,
                default="16:9",
                values=list(cls._ratios),
                title="宽高比",
                description="文生视频使用。图生视频跟随首帧,不单独传纵横比。",
            ),
            "resolution": PortSpec(
                type=PortType.ENUM,
                default=cls._resolutions[0],
                values=list(cls._resolutions),
                title="分辨率",
                description="输出清晰度",
            ),
            "duration": PortSpec(
                type=PortType.INTEGER,
                default=5,
                minimum=cls._duration[0],
                maximum=cls._duration[1],
                title="时长",
                description=f"秒,{cls._duration[0]}~{cls._duration[1]}。部分模型只接受 5 或 10。",
            ),
            "generate_audio": PortSpec(
                type=PortType.BOOLEAN,
                default=True,
                title="有声",
                description=cls._audio_help,
            ),
            "watermark": PortSpec(
                type=PortType.BOOLEAN,
                default=False,
                title="水印",
                description="是否同时生成带水印的结果",
            ),
        }
        if cls._allow_video:
            inputs["video_refs"] = PortSpec(
                type=PortType.LIST,
                item_type=PortType.VIDEO,
                max_items=cls._max_videos,
                title="参考视频",
                description="只接受可访问的 URL。video_role=feature 为特征参考,base 为待编辑视频。",
            )
            inputs["video_role"] = PortSpec(
                type=PortType.ENUM,
                default="feature",
                values=["feature", "base"],
                title="视频角色",
                description=cls._video_role_help,
            )
        if cls._name == "kling-4.0":
            inputs["frame_roles"] = PortSpec(
                type=PortType.LIST,
                item_type=PortType.TEXT,
                max_items=10,
                title="关键帧角色",
                description=(
                    "与参考图片一一对应:first/middle/last/refer。"
                    "缺省按位置:首图首帧、末图尾帧、其余中间帧。refer 为纯参考图(走 Omni)。"
                ),
            )
        if cls._name == "kling-3.0":
            inputs["multi_shot"] = PortSpec(
                type=PortType.BOOLEAN,
                default=True,
                title="多镜头",
                description="关闭后,提示词里的分镜格式也不会切成多镜头。",
            )
            inputs["omni"] = PortSpec(
                type=PortType.BOOLEAN,
                default=False,
                title="Omni",
                description="打开后走 Omni 接口。有参考视频、主体或 3 张以上图片时也会自动走 Omni。",
            )
        return NodeDef(
            name=cls._name,
            display_name=cls._display,
            task_type=TaskType.VIDEO,
            backend="kling",
            backend_model=cls._name,
            point_cost=cls._point_cost,
            description=cls._description,
            knowledge_content=cls._knowledge,
            requirements=["Kling_API_Key"],
            mode="programmatic",
            inputs=inputs,
            outputs={"video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频")},
            capabilities=CapabilityManifest(supported_tasks=["video"], mode="async_poll", priority=cls._priority),
        )

    def validate(self, request: GenerationRequest) -> None:
        super().validate(request)
        prepare(self.name, request)

    def estimate_cost(self, request: GenerationRequest) -> int:
        return cny_to_points(prepare(self.name, request, for_estimate=True).yuan)

    def point_range(self) -> tuple[int, int]:
        return video_point_bounds(self.name)

    def channel_bindings(self) -> list[ChannelBinding]:
        return bindings_for(self.name)

    async def unavailable_reason(self) -> str:
        for binding in self.channel_bindings():
            if await binding.channel.check_available():
                continue
            return await binding.channel.unavailable_reason()
        return f"{self.display_name} 暂不可用"


class Kling40Video(_KlingVideo):
    _name = "kling-4.0"
    _display = "Kling 4.0 & 4.0 Omni"
    _description = "文生、灵活关键帧(最多 10 帧)或 Omni 参考,3~30 秒,720p/1080p"
    _knowledge = (
        "0 张文生;图片默认首图=首帧、末图=尾帧、其余=中间帧,frame_roles 可逐张指定 "
        "first/middle/last/refer。refer 图、参考视频(URL,至多 5 段)、主体或音色(至多 3 个)"
        "走 Omni,素材合计 ≤15。文生选 adaptive 宽高比也走 Omni。素材在提示词里用 "
        "@image_N/@video_N/@element_N/@voice_N 引用。声音写进提示词,没有独立开关和静音档。"
        "有参考视频时按 input_video_duration 计入输入时长计费。"
    )
    _resolutions = ["1080p", "720p"]
    _duration = (3, 30)
    _max_images = 10
    _max_videos = 5
    _allow_video = True
    _ratios = _RATIOS_40
    _point_cost = 105
    _audio_help = "4.0 的声音写在提示词(对白/环境声/配乐),没有独立开关;此项不改变上游请求与积分。"
    _images_help = (
        "0~10 张。0 张文生;默认首图=首帧、末图=尾帧、其余=中间帧;"
        "frame_roles 可逐张指定 first/middle/last/refer(纯参考图)。"
    )
    _video_role_help = "feature: 特征参考,至多 5 段,可与关键帧同用。base: 待编辑视频,只能 1 段,不能配关键帧。"
    _priority = 53


class Kling30TurboVideo(_KlingVideo):
    _name = "kling-3.0-turbo"
    _display = "Kling 3.0 Turbo"
    _description = "文生或单张首帧,3~15 秒,720p/1080p,按有声计价"
    _knowledge = "只支持文生和一张首帧。没有尾帧、参考视频和 4k。多镜头写在提示词里。"
    _max_images = 1
    _point_cost = 56
    _audio_help = "该模型没有静音档,开关不改变请求,仍按有声计价。"
    _priority = 52


class Kling30Video(_KlingVideo):
    _name = "kling-3.0"
    _display = "Kling 3.0 & 3.0 Omni"
    _description = "文生、首尾帧或 Omni 参考,3~15 秒,最高 4k"
    _knowledge = "没有参考视频、主体和第三张图时走文生或图生。否则走 Kling 3.0 Omni。特征参考视频固定无声。"
    _resolutions = ["720p", "1080p", "4k"]
    _max_images = 7
    _allow_video = True
    _point_cost = 63
    _audio_help = "打开为 native。特征参考视频会固定关闭声音。"
    _priority = 51


class KlingO1Video(_KlingVideo):
    _name = "kling-o1"
    _display = "Kling O1"
    _description = "Omni 视频,3~10 秒,720p/1080p。纯文生和单首帧只支持 5 或 10 秒"
    _knowledge = "始终走 Omni。不能生成新音轨;有参考视频且打开有声时保留原声。"
    _duration = (3, 10)
    _max_images = 7
    _allow_video = True
    _point_cost = 42
    _audio_help = "没有 native。有参考视频时打开表示保留原声,否则关闭。"
    _priority = 50


class Kling26Video(_KlingVideo):
    _name = "kling-2.6"
    _display = "Kling 2.6"
    _description = "文生、首帧或首尾帧,只支持 5 或 10 秒"
    _knowledge = "有声只支持 1080p。首尾帧只支持 1080p 且必须无声。音色通过 voice_id 传入,最多 2 个。"
    _duration = (5, 10)
    _max_images = 2
    _point_cost = 70
    _audio_help = "打开后分辨率必须是 1080p。首尾帧请关闭。"
    _priority = 49


class Kling25TurboVideo(_KlingVideo):
    _name = "kling-2.5-turbo"
    _display = "Kling 2.5 Turbo"
    _description = "无声文生、首帧或首尾帧,只支持 5 或 10 秒"
    _knowledge = "没有声音字段。首尾帧只支持 1080p。"
    _duration = (5, 10)
    _max_images = 2
    _point_cost = 21
    _audio_help = "该模型只出无声视频,开关不生效。"
    _priority = 48


KLING_VIDEO_MODELS = [
    Kling40Video,
    Kling30TurboVideo,
    Kling30Video,
    KlingO1Video,
    Kling26Video,
    Kling25TurboVideo,
]

__all__ = [
    "KLING_VIDEO_MODELS",
    "Kling25TurboVideo",
    "Kling26Video",
    "Kling30TurboVideo",
    "Kling30Video",
    "Kling40Video",
    "KlingO1Video",
]
