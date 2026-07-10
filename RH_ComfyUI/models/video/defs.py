"""models/video/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from .overrides import Wan22VideoModel, SeedanceVideoModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.video import wan_videogen_mapper as _wan_videogen_mapper


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
                    default="16:9",
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
                        "无限画布可改用 ordered_content 的 role 字段逐图指定角色"
                    ),
                ),
                "ratio": PortSpec(
                    type=PortType.ENUM,
                    default="adaptive",
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


class Seedance2MiniDef(SeedanceVideoModel):
    """Seedance 2.0 Mini — 轻量低成本档(统一对外模型;当前仅外部供应商提供通道)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        base = Seedance2Def.node_def()
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
                "请求规范与 Seedance 2.0 完全一致:按输入自动切换 文生/图生/首尾帧/多模态。"
                "\n"
                "适用场景:低成本批量生成、对画质要求不苛刻的场景。"
                "\n"
                "注:官方 ARK 暂无 Mini 档,通道由外部供应商插件提供;"
                "\n"
                "未安装/未配置外部供应商时本模型不可用。"
                "\n"
            ),
            requirements=[],
            # ark 暂无 Mini 档;不挂 runninghub(端点即 2.0 标准档)。
            # 该通道由外部供应商插件经 channel_registry.register_binding() 注入。
            backend_model=None,
            backend_models={},
            mode="declarative",
            inputs=dict(base.inputs),
            outputs=dict(base.outputs),
            capabilities=CapabilityManifest(
                supported_tasks=["video"],
                mode="async_poll",
                priority=80,
            ),
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
                    default="16:9",
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
                priority=70,
            ),
        )


ALL_MODELS = [Seedance15ProDef, Seedance2Def, Seedance2MiniDef, Seedance2FastDef, Wan22VideogenDef]
