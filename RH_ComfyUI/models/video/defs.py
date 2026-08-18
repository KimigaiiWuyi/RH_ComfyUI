"""models/video/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from .overrides import (
    Wan22VideoModel,
    Wan30VideoModel,
    SeedanceVideoModel,
    MiniMaxH3VideoModel,
    HappyHorseVideoModel,
    Seedance25VideoModel,
)
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.video import wan_videogen_mapper as _wan_videogen_mapper
from ...utils.mappers.extra_billing import estimate_wan22_points
from ...utils.mappers.wan30_billing import estimate_wan30_points
from ...utils.mappers.seedance_billing import (
    estimate_seedance2_points,
    estimate_seedance25_points,
    estimate_seedance2_fast_points,
    estimate_seedance2_mini_points,
    estimate_seedance15_pro_points,
    input_video_duration_from_params,
)
from ...utils.mappers.happyhorse_billing import estimate_happyhorse_points
from ...utils.mappers.minimax_h3_billing import estimate_minimax_h3_points


class Seedance15ProDef(SeedanceVideoModel):
    """Seedance 1.5 Pro — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="seedance15_pro",
            display_name="Seedance 1.5 Pro",
            task_type=TaskType("video"),
            backend="seedance",
            point_cost=18,
            description="Seedance 1.5 Pro 视频生成,支持离线推理(flex)",
            knowledge_content=(
                "Seedance 1.5 Pro 视频生成。"
                "\n"
                "能力:"
                "\n"
                "- 离线推理(flex service_tier):价格仅在线推理 50%"
                "\n"
                "- 1080p 分辨率"
                "\n"
                "- 按输入自动决定形态:0 张=文生 / 1 张=图生 / 2 张=首尾帧 / 多图+音视频=多模态"
                "\n"
                "支持时长:4~12 秒。"
                "\n"
                "适用场景:成本敏感的生产场景。"
                "\n"
                "注:样片(Draft)模式不再提供独立节点,需在调用前设置"
                "\n"
                '`request.params["draft"] = True`,成本仅在线推理 60%,'
                "\n"
                "但仅支持 480p 且不支持尾帧/flex。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            backend_model="doubao-seedance-1-5-pro-251215",
            backend_models={
                "ark": "doubao-seedance-1-5-pro-251215",
                "runninghub": "",
            },
            mode="declarative",
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="视频生成提示词"),
                # 与 Seedance 2.0 一致:按输入自动决定 文生/图生/首尾帧/多模态
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=9,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生 / 1 张=首帧 / 2 张=首尾帧 / 更多=参考",
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.VIDEO,
                    title="参考视频",
                    description='参考视频,prompt 中用 "视频1/视频2/..." 引用',
                ),
                "audio_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.AUDIO,
                    title="参考音频",
                    description='参考音频,prompt 中用 "音频1/音频2/..." 引用',
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_last", "reference"],
                    title="多图角色",
                    description="多图角色:auto=2 图默认首尾帧 / first_last=强制首尾帧 / reference=全部参考",
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"],
                    title="宽高比",
                    description="视频宽高比",
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="720p",
                    values=["480p", "720p", "1080p"],
                    title="分辨率",
                    description="视频分辨率",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER, default=5, minimum=4, maximum=12, title="时长", description="视频时长,单位秒"
                ),
                "seed": PortSpec(type=PortType.INTEGER, title="随机种子", description="随机种子"),
                "generate_audio": PortSpec(
                    type=PortType.BOOLEAN, default=True, title="同步音频", description="是否生成同步音频"
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="AI 水印", description="是否添加 AI 水印"
                ),
                "camera_fixed": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="固定镜头", description="摄像机是否固定"
                ),
                "return_last_frame": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="返回尾帧", description="是否同时返回尾帧图"
                ),
                "service_tier": PortSpec(
                    type=PortType.ENUM,
                    default="default",
                    values=["default", "flex"],
                    title="推理档位",
                    description=(
                        "推理档位:\n  - default: 在线推理,响应快\n  - flex: 离线推理,价格 50%,适合对时延不敏感的场景"
                    ),
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4)"),
                "last_frame": PortSpec(type=PortType.OUTPUT_IMAGE, description="视频尾帧图"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=75,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按 token 用量计费(有声 16 元/M,无声 8 元/M)。"""
        resolution = request.params.get("resolution", "720p")
        duration = float(request.duration or 5)
        generate_audio = request.params.get("generate_audio", True)
        return estimate_seedance15_pro_points(
            resolution,
            duration,
            generate_audio=generate_audio,
            video_refs=request.video_refs,
            input_video_duration=input_video_duration_from_params(request.params),
        )

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(480p + 4s + 无声) ~ 最大(1080p + 12s + 有声 + 输入 15s)。"""
        return (
            estimate_seedance15_pro_points("480p", 4, generate_audio=False, video_refs=None),
            estimate_seedance15_pro_points("1080p", 12, generate_audio=True, input_video_duration=15.0),
        )


class Seedance2Def(SeedanceVideoModel):
    """Seedance 2.0 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="seedance2",
            display_name="Seedance 2.0",
            task_type=TaskType("video"),
            backend="seedance",
            point_cost=20,
            description="Seedance 2.0 统一视频生成,按输入自动切换 文生/图生/首尾帧/多模态,无需手动区分",
            knowledge_content=(
                "字节跳动 Seedance 2.0 统一视频生成节点。"
                "\n"
                "一个节点覆盖全部视频生成形态,由输入自动决定:"
                "\n"
                "  - 不传图       → 文生视频"
                "\n"
                "  - 传 1 张图     → 图生视频(该图作为首帧)"
                "\n"
                "  - 传 2 张图     → 首尾帧生视频(图1=首帧, 图2=尾帧, 其余作参考)"
                "\n"
                '  - 传图+音/视频  → 多模态参考生视频(prompt 中用 "图片1/视频1/音频1" 代号引用素材)'
                "\n"
                "多图想全部作参考(而非首尾帧)时,传 frame_mode=reference。"
                "\n"
                "优势:中文支持优秀、动作流畅,支持 480p/720p/1080p,"
                "\n"
                "支持 16:9 / 9:16 / 1:1 / 4:3 / 3:4 / 21:9 / adaptive 比例,"
                "\n"
                "默认生成同步音频(generate_audio=false 可关),支持摄像机固定、水印、seed、可选尾帧等。"
                "\n"
                "支持时长:4~15 秒。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            backend_model="doubao-seedance-2-0-260128",
            backend_models={"ark": "doubao-seedance-2-0-260128", "runninghub": ""},
            mode="declarative",
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description=(
                        '视频生成提示词。多模态场景可用 "图片1"/"图片2"/"视频1"/"音频1"\n等代号引用下方传入的素材。'
                    ),
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=9,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生 / 1 张=首帧 / 2 张=首尾帧 / 更多=参考",
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.VIDEO,
                    title="参考视频",
                    description='参考视频,prompt 中用 "视频1/视频2/..." 引用',
                ),
                "audio_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.AUDIO,
                    title="参考音频",
                    description='参考音频,prompt 中用 "音频1/音频2/..." 引用',
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_last", "reference"],
                    title="多图角色",
                    description=(
                        "多图时图片角色的判定方式:\n"
                        "  - auto: 自动,2 张图默认视为首尾帧\n"
                        "  - first_last: 强制首尾帧,图1=首帧, 图2=尾帧, 其余参考\n"
                        "  - reference: 全部图片仅作参考素材,多参考生成\n"
                        "调用方可改用 ordered_content 的 role 字段逐图指定角色"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"],
                    title="宽高比",
                    description="视频宽高比,adaptive 跟随首帧",
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="720p",
                    values=["480p", "720p", "1080p"],
                    title="分辨率",
                    description="视频分辨率",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER, default=5, minimum=4, maximum=15, title="时长", description="视频时长,单位秒"
                ),
                "seed": PortSpec(type=PortType.INTEGER, title="随机种子", description="随机种子,留空则随机"),
                "generate_audio": PortSpec(
                    type=PortType.BOOLEAN, default=True, title="同步音频", description="是否生成同步音频"
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="AI 水印", description="是否添加 AI 水印"
                ),
                "camera_fixed": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="固定镜头", description="摄像机是否固定"
                ),
                "return_last_frame": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="返回尾帧", description="是否同时返回尾帧图"
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4)"),
                "last_frame": PortSpec(
                    type=PortType.OUTPUT_IMAGE, description="视频尾帧图(仅在 return_last_frame=true 时返回)"
                ),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=90,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:token = (输入视频时长 + 输出时长) × 宽 × 高 × fps / 1024。"""
        resolution = request.params.get("resolution", "720p")
        duration = float(request.duration or 5)
        return estimate_seedance2_points(
            resolution,
            duration,
            video_refs=request.video_refs,
            input_video_duration=input_video_duration_from_params(request.params),
        )

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(480p + 4s + 无输入) ~ 最大(各档扫描 + 输入 15s)。

        注意:4K 费率最低但像素最多,实际需要比较各档位的积分值。
        """
        candidates = []
        for res in ("480p", "720p", "1080p", "4k"):
            for dur in (4, 15):
                candidates.append(estimate_seedance2_points(res, dur, video_refs=None))
                candidates.append(estimate_seedance2_points(res, dur, input_video_duration=15.0))
        return (min(candidates), max(candidates))


class Seedance25Def(Seedance25VideoModel):
    """Seedance 2.5 — 火山方舟 doubao-seedance-2-5-260628

    与 seedance2 / seedance2_fast 类型分开;复用 Seedance_apikey_ark。
    能力差异(相对 2.0):
      - 输出时长 4~30 秒,编辑/延长可用 -1 跟随输入
      - 分辨率仅 480p / 720p
      - 多模态参考上限 50(图 30 + 视频 10 + 音频 10)
      - 支持 output_format=mp4|mov
      - task_mode 可显式选择 generate(auto)/edit/extend
      - 不支持 camera_fixed(官方仅 Seedance 1.x)
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="seedance2.5",
            display_name="Seedance 2.5",
            task_type=TaskType("video"),
            backend="seedance",
            # 静态兜底 ≈ 720p 5s 无输入视频(官方示例 7.56 元 = 756 积分)
            point_cost=756,
            description=(
                "Seedance 2.5 统一视频生成:最长 30 秒、最多 50 多模态参考,"
                "支持文生/图生/首尾帧/多模态/编辑/延长;复用火山方舟 Key"
            ),
            knowledge_content=(
                "字节跳动 Seedance 2.5(Model ID: doubao-seedance-2-5-260628)。"
                "\n"
                "与 Seedance 2.0 类型分开的新一代模型,凭证复用火山方舟 Ark Key。"
                "\n"
                "任务形态(task_mode):"
                "\n"
                "  - auto(默认): 按输入自动 文生/图生/首尾帧/多模态"
                "\n"
                "  - edit: 视频编辑(需参考视频;建议 ratio=adaptive, duration=-1)"
                "\n"
                "  - extend: 视频延长(需参考视频;建议 ratio=adaptive)"
                "\n"
                "多图角色(frame_mode)与 2.0 相同:auto / first_last / reference。"
                "\n"
                "优势:最长 30 秒连贯直出、50 个多模态参考、mov/mp4 输出、编辑与延长。"
                "\n"
                "限制:仅 480p/720p;不支持 camera_fixed(固定镜头,官方仅 1.x);"
                "\n"
                "编辑/延长/首尾帧必须 ratio=adaptive,否则上游异步报错。"
                "\n"
                "支持时长:4~30 秒,或 -1 自动(所有 task_mode / frame_mode 均可)。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            backend_model="doubao-seedance-2-5-260628",
            # 仅挂 ark(官方 2.5);不挂 runninghub 端点(端点即 2.0 标准档)
            backend_models={"ark": "doubao-seedance-2-5-260628"},
            mode="declarative",
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description=(
                        '视频生成提示词。多模态可用 "图片1"/"视频1"/"音频1" 代号引用素材;'
                        "编辑/延长任务避免误用对方关键词以免触发错误任务类型。"
                    ),
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=30,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description=("参考图片(最多 30 张):0 张=文生 / 1 张=首帧 / 2 张=首尾帧 / 更多=参考"),
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=10,
                    item_type=PortType.VIDEO,
                    title="参考视频",
                    description='参考视频(最多 10 段),prompt 中用 "视频1/视频2/..." 引用;编辑/延长必需',
                ),
                "audio_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=10,
                    item_type=PortType.AUDIO,
                    title="参考音频",
                    description='参考音频(最多 10 段),prompt 中用 "音频1/音频2/..." 引用',
                ),
                "task_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "edit", "extend"],
                    title="任务类型",
                    description=(
                        "任务类型(与 2.0 类型分开的显式开关):\n"
                        "  - auto: 按输入自动 文生/图生/首尾帧/多模态\n"
                        "  - edit: 视频编辑(需参考视频;ratio 须 adaptive,duration 建议 -1)\n"
                        "  - extend: 视频延长(需参考视频;ratio 须 adaptive)"
                    ),
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_last", "reference"],
                    title="多图角色",
                    description=(
                        "多图时图片角色:\n"
                        "  - auto: 2 张图默认首尾帧\n"
                        "  - first_last: 强制首尾帧(须 ratio=adaptive)\n"
                        "  - reference: 全部图片仅作参考"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="adaptive",
                    values=["adaptive", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
                    title="宽高比",
                    description=(
                        "视频宽高比;默认 adaptive。"
                        "编辑/延长/首帧·首尾帧必须用 adaptive,自定义比例会异步报错;"
                        "纯文生与多模态参考可用固定比例"
                    ),
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="720p",
                    values=["480p", "720p"],
                    title="分辨率",
                    description="视频分辨率(2.5 仅支持 480p / 720p)",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER,
                    default=5,
                    minimum=-1,
                    maximum=30,
                    title="时长",
                    description=("输出时长(秒):4~30;填 -1 表示自动(所有模式均可,有参考视频时跟随输入)"),
                ),
                "seed": PortSpec(type=PortType.INTEGER, title="随机种子", description="随机种子,留空则随机"),
                "generate_audio": PortSpec(
                    type=PortType.BOOLEAN, default=True, title="同步音频", description="是否生成同步音频"
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="AI 水印", description="是否添加 AI 水印"
                ),
                # 官方 camera_fixed 仅 1.x;2.5 强校验 400(见火山方舟创建任务 API)
                "return_last_frame": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="返回尾帧", description="是否同时返回尾帧图"
                ),
                "output_format": PortSpec(
                    type=PortType.ENUM,
                    default="mp4",
                    values=["mp4", "mov"],
                    title="输出格式",
                    description="输出视频容器格式(2.5 支持 mp4 / mov)",
                ),
                "omni_reference_task_type": PortSpec(
                    type=PortType.ENUM,
                    default="reference",
                    values=["reference", "edit", "extend"],
                    title="任务类型引导",
                    description=(
                        "官方 omni_reference_task_type(与 duration 同级,仅 2.5 多模态参考):\n"
                        "  - reference: 多参考生成(同步校验;默认)\n"
                        "  - edit: 视频编辑(须与 task_mode=edit 一致)\n"
                        "  - extend: 视频延长(须与 task_mode=extend 一致)\n"
                        "不传 auto。文生 / 首帧 / 首尾帧不得写入(上游 TaskTypeConstraint)。"
                    ),
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4/MOV)"),
                "last_frame": PortSpec(
                    type=PortType.OUTPUT_IMAGE, description="视频尾帧图(仅在 return_last_frame=true 时返回)"
                ),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=92,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:token = (输入视频时长 + 输出时长) × 宽 × 高 × fps / 1024。

        输入时长优先读 params.input_video_duration,否则累加 video_refs 各段时长
        (未知段按 5s);duration=-1 时输出时长跟输入总时长。
        """
        resolution = request.params.get("resolution") or request.resolution or "720p"
        duration = request.duration
        if duration is None:
            duration = request.params.get("duration", 5)
        try:
            duration_f = float(duration)
        except (TypeError, ValueError):
            duration_f = 5.0
        return estimate_seedance25_points(
            str(resolution),
            duration_f,
            video_refs=request.video_refs,
            input_video_duration=input_video_duration_from_params(request.params),
        )

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(480p + 4s + 无输入) ~ 最大(720p + 30s + 输入 150s=10×15)。"""
        return (
            estimate_seedance25_points("480p", 4, video_refs=None),
            estimate_seedance25_points("720p", 30, input_video_duration=150.0),
        )


class Seedance2MiniDef(SeedanceVideoModel):
    """Seedance 2.0 Mini — 轻量低成本档

    与 Seedance 2.0 / Fast 同一套代码路径,仅 vendor model id 与计费费率不同。
    官方 Model ID: ``doubao-seedance-2-0-mini-260615``(仅 480p/720p)。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        # 输入面与 Fast 对齐(480p/720p;其余与 2.0 同构)
        base = Seedance2FastDef.node_def()
        return NodeDef(
            name="seedance2_mini",
            display_name="Seedance 2.0 Mini",
            task_type=TaskType("video"),
            backend="seedance",
            point_cost=10,
            description="Seedance 2.0 Mini 视频生成,轻量低成本版(请求规范与 Seedance 2.0 一致)",
            knowledge_content=(
                "Seedance 2.0 Mini 是 Seedance 2.0 的轻量低成本版本。"
                "\n"
                "请求规范与 Seedance 2.0 / Fast 一致:按输入自动切换 文生/图生/首尾帧/多模态。"
                "\n"
                "仅支持 480p 与 720p 分辨率(不支持 1080p)。"
                "\n"
                "适用场景:低成本批量生成、对画质要求不苛刻的场景。"
                "\n"
                "官方 Model ID: doubao-seedance-2-0-mini-260615;复用火山方舟 Ark Key。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            backend_model="doubao-seedance-2-0-mini-260615",
            backend_models={
                "ark": "doubao-seedance-2-0-mini-260615",
                "runninghub": "",
            },
            mode="declarative",
            inputs=dict(base.inputs),
            outputs=dict(base.outputs),
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=80,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按 token 用量计费(23 元/M 无输入,14 元/M 有输入)。"""
        resolution = request.params.get("resolution", "720p")
        duration = float(request.duration or 5)
        return estimate_seedance2_mini_points(
            resolution,
            duration,
            video_refs=request.video_refs,
            input_video_duration=input_video_duration_from_params(request.params),
        )

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(480p + 4s + 无输入) ~ 最大(720p + 15s + 输入 15s)。"""
        return (
            estimate_seedance2_mini_points("480p", 4, video_refs=None),
            estimate_seedance2_mini_points("720p", 15, input_video_duration=15.0),
        )


class Seedance2FastDef(SeedanceVideoModel):
    """Seedance 2.0 Fast — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="seedance2_fast",
            display_name="Seedance 2.0 Fast",
            task_type=TaskType("video"),
            backend="seedance",
            point_cost=15,
            description="Seedance 2.0 Fast 视频生成,速度更快、价格更低(不支持 1080p)",
            knowledge_content=(
                "Seedance 2.0 Fast 是 Seedance 2.0 的快速版本,价格更低、生成更快。"
                "\n"
                "仅支持 480p 与 720p 分辨率(不支持 1080p)。"
                "\n"
                "输入形态由系统自动决定:"
                "\n"
                "  - 不传图 → 文生视频"
                "\n"
                "  - 1 张图 → 图生视频(首帧)"
                "\n"
                "  - 2+张图 → 首尾帧 / 多模态参考"
                "\n"
                "适用场景:快速预览、低成本批量生成。"
                "\n"
            ),
            requirements=["seedance_apikey"],
            backend_model="doubao-seedance-2-0-fast-260128",
            backend_models={
                "ark": "doubao-seedance-2-0-fast-260128",
                "runninghub": "",
            },
            mode="declarative",
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="提示词", description="视频生成提示词"),
                # 与 Seedance 2.0 一致:按输入自动决定 文生/图生/首尾帧/多模态
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=9,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生 / 1 张=首帧 / 2 张=首尾帧 / 更多=参考",
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.VIDEO,
                    title="参考视频",
                    description='参考视频,prompt 中用 "视频1/视频2/..." 引用',
                ),
                "audio_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.AUDIO,
                    title="参考音频",
                    description='参考音频,prompt 中用 "音频1/音频2/..." 引用',
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_last", "reference"],
                    title="多图角色",
                    description="多图角色:auto=2 图默认首尾帧 / first_last=强制首尾帧 / reference=全部参考",
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="9:16",
                    values=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"],
                    title="宽高比",
                    description="视频宽高比",
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="720p",
                    values=["480p", "720p"],
                    title="分辨率",
                    description="视频分辨率,Fast 版不支持 1080p",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER, default=5, minimum=4, maximum=15, title="时长", description="视频时长,单位秒"
                ),
                "seed": PortSpec(type=PortType.INTEGER, title="随机种子", description="随机种子"),
                "generate_audio": PortSpec(
                    type=PortType.BOOLEAN, default=True, title="同步音频", description="是否生成同步音频"
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="AI 水印", description="是否添加 AI 水印"
                ),
                "camera_fixed": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="固定镜头", description="摄像机是否固定"
                ),
                "return_last_frame": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="返回尾帧", description="是否同时返回尾帧图"
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4)"),
                "last_frame": PortSpec(type=PortType.OUTPUT_IMAGE, description="视频尾帧图"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=85,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按 token 用量计费(37 元/M 无输入,22 元/M 有输入)。"""
        resolution = request.params.get("resolution", "720p")
        duration = float(request.duration or 5)
        return estimate_seedance2_fast_points(
            resolution,
            duration,
            video_refs=request.video_refs,
            input_video_duration=input_video_duration_from_params(request.params),
        )

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(480p + 4s + 无输入) ~ 最大(720p + 15s + 输入 15s)。"""
        return (
            estimate_seedance2_fast_points("480p", 4, video_refs=None),
            estimate_seedance2_fast_points("720p", 15, input_video_duration=15.0),
        )


class HappyHorse11Def(HappyHorseVideoModel):
    """HappyHorse 1.1 — 统一视频入口(文生/图生/参考/编辑),对外仅暴露 happyhorse1.1

    内部按 input_schema 自动适配供应商 model:
      0 图 → happyhorse-1.1-t2v
      1 图 → happyhorse-1.1-i2v
      2~9 图 / frame_mode=reference / 视频作参考 → happyhorse-1.1-r2v
      显式 task_mode=edit → happyhorse-1.1-video-edit
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="happyhorse1.1",
            display_name="HappyHorse 1.1",
            task_type=TaskType("video"),
            backend="happyhorse",
            point_cost=500,
            description=(
                "HappyHorse 1.1 统一视频生成:按输入自动切换 文生 / 图生(首帧) / 多图参考 / 视频编辑,无需手动选子模型"
            ),
            knowledge_content=(
                "阿里云 DashScope HappyHorse 1.1 统一视频节点。"
                "\n"
                "一个节点覆盖全部形态,由输入自动决定供应商 model:"
                "\n"
                "  - 不传图           → 文生视频 (happyhorse-1.1-t2v)"
                "\n"
                "  - 传 1 张图         → 图生视频首帧 (happyhorse-1.1-i2v)"
                "\n"
                "  - 传 2~9 张图       → 参考生视频 (happyhorse-1.1-r2v);"
                "prompt 中用 [Image 1]/[Image 2] 引用"
                "\n"
                "  - 显式 task_mode=edit + 输入视频 → 视频编辑 (happyhorse-1.1-video-edit)"
                "\n"
                "  - 非编辑模式禁止传视频(r2v 只收 reference_image)"
                "\n"
                "frame_mode=reference 时,即使只有 1 张图也走参考生而非首帧。"
                "\n"
                "分辨率 480p/720p/1080p(视频编辑仅 720p/1080p),时长 3~15 秒,"
                "宽高比 16:9/9:16/1:1/4:3/3:4/4:5/5:4/9:21/21:9"
                "(图生视频宽高比跟随首帧)。"
                "\n"
                "原生输出有声视频;不支持参考音频;无独立首尾帧端点。"
                "\n"
            ),
            requirements=["happyhorse_apikey"],
            # 逻辑名;真实 vendor model 由通道按形态解析
            backend_model="happyhorse1.1",
            backend_models={"dashscope": "happyhorse1.1"},
            mode="declarative",
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description=(
                        "视频描述。参考生视频可用 [Image 1]/[Image 2] 引用下方图片;"
                        "也支持「图片1」写法,系统会自动改写。"
                        "图生视频可不传(模型根据首帧推断运动)。"
                    ),
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=9,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description=(
                        "图片输入自动决定形态:\n"
                        "  - 0 张 = 文生视频\n"
                        "  - 1 张 = 图生视频(该图作为首帧)\n"
                        "  - 2~9 张 = 参考生视频(多主体融合)\n"
                        "frame_mode=reference 时 1 张也走参考生"
                    ),
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=1,
                    item_type=PortType.VIDEO,
                    title="输入视频",
                    description=(
                        "仅视频编辑(task_mode=edit)可传入 1 段视频;"
                        "多参考 / 首尾帧模式禁止传视频。"
                        "编辑时可附 0~5 张参考图。输入时长 3~60 秒,输出上限 15 秒。"
                    ),
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_frame", "reference"],
                    title="图片角色",
                    description=(
                        "图片角色判定:\n"
                        "  - auto: 1 图=首帧图生, ≥2 图=参考生\n"
                        "  - first_frame: 强制图生视频(仅用第 1 张作首帧)\n"
                        "  - reference: 全部图片/视频作参考素材(r2v)"
                    ),
                ),
                "task_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "edit"],
                    title="任务模式",
                    description=(
                        "任务形态:\n"
                        "  - auto: 按输入自动(文生/图生/多参考);有视频也走 r2v 参考\n"
                        "  - edit: 视频编辑(必须提供 1 段输入视频)"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="16:9",
                    values=[
                        "16:9",
                        "9:16",
                        "1:1",
                        "4:3",
                        "3:4",
                        "4:5",
                        "5:4",
                        "9:21",
                        "21:9",
                    ],
                    title="宽高比",
                    description="视频宽高比(图生视频跟随首帧,本参数不生效)",
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="720p",
                    values=["480p", "720p", "1080p"],
                    title="分辨率",
                    description="视频分辨率;视频编辑模式仅支持 720p / 1080p",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER,
                    default=5,
                    minimum=3,
                    maximum=15,
                    title="时长",
                    description="输出视频时长(秒),3~15;视频编辑模式由输入视频决定",
                ),
                "seed": PortSpec(
                    type=PortType.INTEGER,
                    title="随机种子",
                    description="随机种子,留空则随机",
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN,
                    default=False,
                    title="水印",
                    description='是否添加 "Happy Horse" 水印(右下角)',
                ),
                "audio_setting": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "origin"],
                    title="声音控制",
                    description=("仅视频编辑模式生效:\n  - auto: 由模型控制\n  - origin: 保留输入视频原声"),
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4, H.264, 24fps)"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=88,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按分辨率 × 输出时长。"""
        resolution = request.params.get("resolution") or request.resolution or "720p"
        duration = float(request.duration or request.params.get("duration") or 5)
        return estimate_happyhorse_points(resolution, duration)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(480p + 3s) ~ 最大(1080p + 15s)。"""
        return (
            estimate_happyhorse_points("480p", 3),
            estimate_happyhorse_points("1080p", 15),
        )


class Wan22VideogenDef(Wan22VideoModel):
    """Wan2.2 视频生成 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)

    2026-07 起 input schema 收紧:Wan 2.2 仅支持 首帧(必需) + 尾帧(可选) 两类图,
    不再假装支持 0~9 张"参考图"。max_items=2 与上游 i2v 工作流
    (WanVideoImageToVideoEncode 仅暴露 start_image) 的事实一致;
    想要 ≥3 张参考的请改走 Seedance 2.0 / Seedance 2.0 Fast(模型名 seedance2*)。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="wan2.2_videogen",
            display_name="Wan2.2 视频生成",
            task_type=TaskType("video"),
            backend="comfyui",
            point_cost=15,
            description=(
                "Wan 2.2 视频生成节点,按输入自动切换:\n"
                "  - 不传图    → 文生视频\n"
                "  - 传 1 张图  → 图生视频(该图作为首帧)\n"
                "  - 传 2 张图  → 首尾帧生视频(图片1=首帧,图片2=尾帧)\n"
                '可在 prompt 中用 "图片1" / "图片2" 引用首/尾帧(支持位置插值)。'
            ),
            knowledge_content=(
                "Wan 2.2 本地 ComfyUI 视频生成。"
                "\n"
                "支持 3 种输入形态:"
                "\n"
                "  - 不传图 → 文生视频"
                "\n"
                "  - 1 张图 → 图生视频(该图作为首帧)"
                "\n"
                "  - 2 张图 → 首尾帧生视频(图片1=首帧,图片2=尾帧,尾帧可缺省)"
                "\n"
                "不支持多参考 / 音视频参考 / 有声视频 / 分辨率 > 720p(像素积约束)。"
                "\n"
                "想要 ≥3 张参考图 / 多模态引用 / 有声视频,请改用 Seedance 2.0 系列。"
                "\n"
                "适用场景:本地部署零 API 费用、中文支持良好、宽高比可任意指定。"
                "\n"
            ),
            requirements=["comfyui_url"],
            workflow_file="wan2.2_t2v.json",
            mode="programmatic",
            mapper_func=_wan_videogen_mapper,
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description=(
                        '视频描述。首尾帧场景可用 "图片1"=首帧 / "图片2"=尾帧 代号\n'
                        "引用下方传入的图片,系统会自动替换为位置标签。"
                    ),
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=2,
                    item_type=PortType.IMAGE,
                    title="首尾帧图片",
                    description=(
                        "图片输入,Wan 2.2 只支持首尾帧:\n"
                        "  - 0 张 = 文生视频\n"
                        "  - 1 张 = 图生视频,该图作为首帧\n"
                        "  - 2 张 = 首尾帧生视频,images[0]=首帧,images[1]=尾帧\n"
                        "超过 2 张需改用 Seedance 2.0 / Seedance 2.0 Fast。"
                    ),
                ),
                "width": PortSpec(type=PortType.INTEGER, default=720, title="宽度", description="视频宽度"),
                "height": PortSpec(type=PortType.INTEGER, default=1280, title="高度", description="视频高度"),
                "duration": PortSpec(type=PortType.INTEGER, default=5, title="时长", description="视频时长,单位秒"),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="sync",
                max_concurrency=1,  # 本地 ComfyUI 共用一块 GPU,工作流必须串行
                priority=70,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输出视频时长计费(0.6 元/秒 = 60 积分/秒)。"""
        duration = float(request.duration or 5)
        return estimate_wan22_points(duration)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(1 秒) ~ 最大(15 秒,输入最大时长)。"""
        return (
            estimate_wan22_points(1),
            estimate_wan22_points(15),
        )


class Wan30Def(Wan30VideoModel):
    """万相 3.0 — DashScope wan3.0-video,能力对齐 Seedance 2.0 + 参考文件

    凭证复用 HappyHorse_apikey_dashscope;须在 DashScope_Enabled_Models 勾选 wan3.0。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="wan3.0",
            display_name="万相 3.0",
            task_type=TaskType("video"),
            backend="wan30",
            point_cost=600,
            description=(
                "万相 3.0 统一视频生成:按输入自动切换 文生/首帧/首尾帧/多参考,"
                "另支持 PDF 等参考文件或网页生视频;复用 DashScope Key"
            ),
            knowledge_content=(
                "阿里云 DashScope 万相 3.0(model: wan3.0-video)。"
                "\n"
                "能力对齐 Seedance 2.0,由输入自动决定形态:"
                "\n"
                "  - 不传图       → 文生视频"
                "\n"
                "  - 传 1 张图     → 图生视频(该图作为首帧)"
                "\n"
                "  - 传 2 张图     → 首尾帧生视频(图1=首帧, 图2=尾帧)"
                "\n"
                "  - 传图+音/视频  → 全能参考生视频(prompt 中用 图1/视频1/音频1 引用)"
                "\n"
                "  - 传 file_url   → 参考文件生视频(PDF/PPT/Word 等,最多 1 个)"
                "\n"
                "  - 传 link_url   → 参考网页生视频(公开网页,与 file_url 互斥)"
                "\n"
                "多图想全部作参考(而非首尾帧)时,传 frame_mode=reference。"
                "\n"
                "首帧/首尾帧不能与参考视频、音频、文件或网页混用。"
                "\n"
                "分辨率 480p/720p/1080p,默认 1080p;时长 2~30 秒或 -1 智能时长;"
                "\n"
                "宽高比 adaptive / 16:9 / 4:3 / 1:1 / 3:4 / 9:16。"
                "\n"
                "原生有声,generate_audio=false 可关(价格相同)。"
                "\n"
                "凭证复用 HappyHorse_apikey_dashscope,须在「启用的 DashScope 模型」勾选 wan3.0。"
                "\n"
            ),
            requirements=["HappyHorse_apikey_dashscope"],
            backend_model="wan3.0-video",
            backend_models={"dashscope": "wan3.0-video"},
            mode="declarative",
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description=(
                        "视频生成提示词。全能参考可用「图1」「视频1」「音频1」指代素材;"
                        "与 media 必填其一。"
                    ),
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=10,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description="参考图片:0 张=文生 / 1 张=首帧 / 2 张=首尾帧 / 更多或带音视频=参考",
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=5,
                    item_type=PortType.VIDEO,
                    title="参考视频",
                    description='参考视频(最多 5 段,合计不超过 15 秒),prompt 中用 "视频1" 引用',
                ),
                "audio_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=5,
                    item_type=PortType.AUDIO,
                    title="参考音频",
                    description='参考音频(最多 5 段,合计不超过 15 秒),prompt 中用 "音频1" 引用',
                ),
                "file_url": PortSpec(
                    type=PortType.STRING,
                    title="参考文件",
                    description=(
                        "参考文件公网 URL(PDF/PPT/Word/Excel/TXT/MD 等,最多 1 个,≤100MB/50 页)。"
                        "与网页链接互斥,且不能与首帧/首尾帧混用。"
                    ),
                ),
                "link_url": PortSpec(
                    type=PortType.STRING,
                    title="参考网页",
                    description="公开网页 URL(无需登录)。与参考文件互斥,且不能与首帧/首尾帧混用。",
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_last", "reference"],
                    title="多图角色",
                    description=(
                        "多图时图片角色:\n"
                        "  - auto: 2 张图默认首尾帧\n"
                        "  - first_last: 强制首尾帧,图1=首帧, 图2=尾帧\n"
                        "  - reference: 全部图片仅作参考素材"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="adaptive",
                    values=["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"],
                    title="宽高比",
                    description="视频宽高比;adaptive 根据输入媒体自动推荐",
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="1080p",
                    values=["480p", "720p", "1080p"],
                    title="分辨率",
                    description="视频分辨率",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER,
                    default=5,
                    minimum=-1,
                    maximum=30,
                    title="时长",
                    description="输出时长(秒):2~30;填 -1 为智能时长。有参考视频时输入+输出不超过 30 秒。",
                ),
                "seed": PortSpec(type=PortType.INTEGER, title="随机种子", description="随机种子,留空则随机"),
                "generate_audio": PortSpec(
                    type=PortType.BOOLEAN,
                    default=True,
                    title="同步音频",
                    description="输出是否包含音轨;开关价格相同",
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN, default=False, title="AI 水印", description="是否添加水印"
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4)"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=89,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:分辨率档位 × 输出秒数;duration=-1 按 15s 预留。"""
        resolution = request.params.get("resolution") or request.resolution or "1080p"
        duration = request.duration
        if duration is None:
            duration = request.params.get("duration", 5)
        try:
            duration_f = float(duration)
        except (TypeError, ValueError):
            duration_f = 5.0
        return estimate_wan30_points(str(resolution), duration_f)

    def point_range(self) -> tuple[int, int]:
        """最小 480p×2s;最大 1080p×30s。"""
        return (
            estimate_wan30_points("480p", 2),
            estimate_wan30_points("1080p", 30),
        )


class MiniMaxH3Def(MiniMaxH3VideoModel):
    """MiniMax H3 — 官方 MiniMax-H3,复用 MiniMax_apikey,需在启用列表勾选"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="minimax_h3",
            display_name="MiniMax H3",
            task_type=TaskType("video"),
            backend="minimax-h3",
            point_cost=400,
            description=(
                "MiniMax H3 四种模式:文生 / 图生(首帧或尾帧) / 首尾帧 / 全能参考,"
                "768P 或 2K 直出,4~15 秒,原生有声;复用 MiniMax API Key"
            ),
            knowledge_content=(
                "MiniMax H3(官方 Model ID: MiniMax-H3)统一视频节点,四种模式与 Seedance 2.0/2.5 同构。"
                "\n"
                "task_mode(可显式指定,默认 auto 按输入自动):"
                "\n"
                "  - auto: 0 图=文生 / 1 图=图生首帧 / 2 图=首尾帧 / 图+音视频或≥3 图=全能参考"
                "\n"
                "  - t2v: 文生视频(仅 prompt;ratio 须为具体比例,不能 adaptive)"
                "\n"
                "  - i2v: 图生视频(1 张图作首帧;frame_mode=last_frame 则作尾帧)"
                "\n"
                "  - first_last: 首尾帧(图1=首帧,图2=尾帧;也可只传一张)"
                "\n"
                "  - reference: 全能参考(图≤9 + 视频≤3 + 音频≤3,合计≤12)"
                "\n"
                "frame_mode 细化图片角色:auto / first_frame / last_frame / first_last / reference。"
                "\n"
                "图生/首尾帧与参考音视频互斥,不可混用。"
                "\n"
                "分辨率 768P / 2K;时长 4~15 秒整数;原生输出立体声音轨。"
                "\n"
                "计费:768P 0.5 元/秒、2K 0.8 元/秒;秒数=输出+输入视频。"
                "\n"
                "输入图前 5 张免费,超出每张 0.2 元。"
                "\n"
                "凭证复用 MiniMax_apikey;须在「启用的 MiniMax 模型」中勾选 minimax_h3。"
                "\n"
                "需按量开通 H3。排队中任务可远程取消;运行中任务上游不可取消。"
                "\n"
            ),
            requirements=["minimax_apikey"],
            backend_model="MiniMax-H3",
            backend_models={"minimax-h3": "MiniMax-H3"},
            mode="declarative",
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="提示词",
                    description=(
                        "视频描述(必填,最长 7000 字符)。"
                        "多模态可用「图片1」「视频1」「音频1」引用下方素材。"
                    ),
                ),
                "images": PortSpec(
                    type=PortType.LIST,
                    min_items=0,
                    max_items=9,
                    item_type=PortType.IMAGE,
                    title="参考图片",
                    description=(
                        "图片输入(最多 9 张)。task_mode=auto 时:\n"
                        "  - 0 张 = 文生\n"
                        "  - 1 张 = 图生(默认首帧;frame_mode=last_frame 则尾帧)\n"
                        "  - 2 张 = 首尾帧\n"
                        "  - ≥3 张或带音视频 = 全能参考"
                    ),
                ),
                "video_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.VIDEO,
                    title="参考视频",
                    description="仅全能参考模式;最多 3 段,单段 2~15 秒,总时长 ≤15 秒;与图生/首尾帧互斥",
                ),
                "audio_refs": PortSpec(
                    type=PortType.LIST,
                    max_items=3,
                    item_type=PortType.AUDIO,
                    title="参考音频",
                    description="仅全能参考模式;最多 3 段,单段 2~15 秒;与图生/首尾帧互斥",
                ),
                "task_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "t2v", "i2v", "first_last", "reference"],
                    title="任务模式",
                    description=(
                        "官方四种模式(与 Seedance 2.0/2.5 的 task_mode 同级):\n"
                        "  - auto: 按输入自动 文生/图生/首尾帧/全能参考\n"
                        "  - t2v: 文生视频(不要传图或音视频)\n"
                        "  - i2v: 图生视频(1 张图,默认首帧)\n"
                        "  - first_last: 首尾帧(图1=首帧,图2=尾帧)\n"
                        "  - reference: 全能参考(图/视频/音频可组合,合计≤12)"
                    ),
                ),
                "frame_mode": PortSpec(
                    type=PortType.ENUM,
                    default="auto",
                    values=["auto", "first_frame", "last_frame", "first_last", "reference"],
                    title="多图角色",
                    description=(
                        "图片角色(与 task_mode 正交,auto 时也可单独用):\n"
                        "  - auto: 1 图=首帧,2 图=首尾帧\n"
                        "  - first_frame: 强制图生首帧\n"
                        "  - last_frame: 强制图生尾帧\n"
                        "  - first_last: 强制首尾帧(不可带参考音视频)\n"
                        "  - reference: 全部图片仅作参考"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="16:9",
                    values=["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"],
                    title="宽高比",
                    description=(
                        "文生视频必须指定具体比例(不能 adaptive);"
                        "图生首尾帧由输入图决定(adaptive);"
                        "参考生默认 adaptive,也可指定"
                    ),
                ),
                "resolution": PortSpec(
                    type=PortType.ENUM,
                    default="2k",
                    values=["768p", "2k"],
                    title="分辨率",
                    description="输出分辨率:768P 或 2K 直出",
                ),
                "duration": PortSpec(
                    type=PortType.INTEGER,
                    default=5,
                    minimum=4,
                    maximum=15,
                    title="时长",
                    description="输出时长(秒),4~15 的整数",
                ),
                "watermark": PortSpec(
                    type=PortType.BOOLEAN,
                    default=False,
                    title="AIGC 水印",
                    description="是否添加 AIGC 标识水印",
                ),
            },
            outputs={
                "video": PortSpec(type=PortType.OUTPUT_VIDEO, description="生成的视频(MP4,原生有声)"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=87,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        resolution = request.params.get("resolution") or request.resolution or "2k"
        duration = request.duration
        if duration is None:
            duration = request.params.get("duration", 5)
        try:
            duration_f = float(duration)
        except (TypeError, ValueError):
            duration_f = 5.0
        n_img = len(request.images or [])
        if request.ordered_content:
            from ...core.schema.types import MediaKind, ContentItemType

            n_oc = sum(
                1
                for item in request.ordered_content
                if item.type == ContentItemType.IMAGE
                or (item.media is not None and item.media.kind == MediaKind.IMAGE)
            )
            n_img = max(n_img, n_oc)
        return estimate_minimax_h3_points(
            str(resolution),
            duration_f,
            video_refs=request.video_refs,
            input_video_duration=input_video_duration_from_params(request.params),
            num_input_images=n_img,
        )

    def point_range(self) -> tuple[int, int]:
        """最小 768P×4s;最大 2K×15s + 输入视频 15s + 9 张图(超出 5 张收费)。"""
        return (
            estimate_minimax_h3_points("768p", 4),
            estimate_minimax_h3_points("2k", 15, input_video_duration=15.0, num_input_images=9),
        )


ALL_MODELS = [
    Seedance15ProDef,
    Seedance2Def,
    Seedance25Def,
    Seedance2MiniDef,
    Seedance2FastDef,
    HappyHorse11Def,
    Wan30Def,
    MiniMaxH3Def,
    Wan22VideogenDef,
]
