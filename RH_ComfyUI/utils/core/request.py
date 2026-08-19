"""统一请求/响应模型 — 覆盖所有 AIGC 任务类型

设计原则:
1. 显式字段优先 — 不再用 `extra: dict` 作为逃生通道,所有后端专属参数都有命名字段
2. 通用包 + 任务专属 payload — 用 `params` 字典做轻量任务级扩展
3. 多模态有序内容 — `ordered_content` 用于 Seedance 2.0 这类支持任意顺序 text+image+video+audio 的接口
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, Optional
from dataclasses import field, dataclass

if TYPE_CHECKING:
    # 仅为类型检查导入,避免运行时循环
    from .types import MediaRef, ContentItem


# ── 旧式 task_type → 新式 TaskType 归一化映射 ──
# 外部 data 目录的 YAML 可能使用旧的细分类型(image2image / text2image 等),
# 但核心路由只按输出模态分类。此映射表在 YAML 加载时将旧值归一化。

_TASK_TYPE_NORMALIZE_MAP: dict[str, str] = {
    # 图片类(输出 = image)
    "text2image": "image",
    "image2image": "image",
    "image_edit": "image",
    "img2img": "image",
    "edit": "image",
    # 视频类(输出 = video)
    "text2video": "video",
    "image2video": "video",
    "img2video": "video",
    "first_last_frame2video": "video",
}


def normalize_task_type(raw: str) -> str:
    """将旧式 task_type 字符串归一化为当前 TaskType 枚举值

    如果 raw 已经是合法的 TaskType 值,直接返回;
    否则查归一化映射表;映射表也没有则返回原值(后续 TaskType() 会抛 ValueError)。
    """
    # 先检查是否已经是合法值
    try:
        TaskType(raw)
        return raw
    except ValueError:
        pass
    return _TASK_TYPE_NORMALIZE_MAP.get(raw, raw)


class TaskType(str, Enum):
    """任务类型 = 输出模态

    路由不再按 文生图/图生图/编辑、文生/图生/首尾帧/多模态 这种细分强制分类,
    而是以"输出模态"作为路由桶,桶内由节点声明的输入端口(inputs)+输入档案
    (图片数量、是否带音/视频参考)来隔离具体能力:
      - IMAGE : 0 张图 → 文生图; >=1 张图 → 图片编辑(已移除潜空间图生图 i2i)
      - VIDEO : 0 图=文生视频 / 1 图=图生视频 / 2 图=首尾帧 / 图+音视频=多模态
      - MUSIC : 音乐生成
      - SPEECH: 语音生成(支持参考音频克隆)
      - ASR   : 语音识别(音频 → 文本)
    """

    IMAGE = "image"
    VIDEO = "video"
    MUSIC = "music"
    SPEECH = "speech"
    ASR = "asr"


class OutputType(str, Enum):
    """输出类型枚举"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"


# 任务类型 → 输出类型 映射
TASK_OUTPUT_MAP: dict[TaskType, OutputType] = {
    TaskType.IMAGE: OutputType.IMAGE,
    TaskType.VIDEO: OutputType.VIDEO,
    TaskType.MUSIC: OutputType.AUDIO,
    TaskType.SPEECH: OutputType.AUDIO,
    TaskType.ASR: OutputType.TEXT,
}

# 任务类型 → MIME 类型 映射
TASK_MIME_MAP: dict[TaskType, str] = {
    TaskType.IMAGE: "image/png",
    TaskType.VIDEO: "video/mp4",
    TaskType.MUSIC: "audio/mpeg",
    TaskType.SPEECH: "audio/mpeg",
    TaskType.ASR: "text/plain",
}

# 任务类型 → 中文名称 映射
TASK_DISPLAY_NAME: dict[TaskType, str] = {
    TaskType.IMAGE: "图片生成",
    TaskType.VIDEO: "视频生成",
    TaskType.MUSIC: "音乐生成",
    TaskType.SPEECH: "语音生成",
    TaskType.ASR: "语音识别",
}

# 目录分组显示名(catalog_group; 与 TaskType 可不同)
# 专用工具模型(多角度/抠图等)挂 "tool",与图片生成主列表分离
CATALOG_GROUP_DISPLAY: dict[str, str] = {
    **{t.value: name for t, name in TASK_DISPLAY_NAME.items()},
    "tool": "杂项工具",
}


# ═══════════════════════════════════════════════════════════════════════
#  GenerationRequest
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class GenerationRequest:
    """统一的 AIGC 生成请求 — 覆盖所有任务类型

    字段矩阵(✅=必有,⭕=可选,空=不适用):

    字段                    | t2i | i2i | edit | t2v | i2v | flf2v | mm2v | music | speech | asr
    ------------------------|-----|-----|------|-----|-----|-------|------|-------|--------|-----
    prompt                  | ✅  | ✅  | ✅   | ✅  | ✅  | ✅    | ✅   | ✅ 风格 | ✅ 文本 |    ⭕
    negative_prompt         | ⭕  | ⭕  |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    images                  |     | ✅1+| ✅1+ |     | ✅1 | ✅2   | ✅1+ |        |        |
    video_refs              |     |     |      |     |     |       | ✅0+ |        |        |
    audio_refs              |     |     |      |     |     |       | ✅0+ |        |        | ✅0+
    ordered_content         |     |     |      |     |     |       | ✅   |        |        |
    reference_audio         |     |     |      |     |     |       |      |        | ⭕    | ✅1
    mood                    |     |     |      |     |     |       |      |        | ⭕    |
    voice_id                |     |     |      |     |     |       |      |        | ⭕    |
    width / height          | ✅  | ✅  |      | ✅  | ✅  | ✅    | ✅   |        |        |
    ratio                   |     |     |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    resolution              |     |     |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    duration                |     |     |      | ✅  | ✅  | ✅    | ✅   |        |        |
    seed                    | ⭕  | ⭕  | ⭕   | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    generate_audio          |     |     |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    watermark               | ⭕  | ⭕  | ⭕   | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    camera_fixed            |     |     |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    return_last_frame       |     |     |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    service_tier            |     |     |      | ⭕  | ⭕  | ⭕    | ⭕   |        |        |
    model                   | ⭕  | ⭕  | ⭕   | ⭕  | ⭕  | ⭕    | ⭕   | ⭕    | ⭕    | ⭕
    speed                   |     |     |      |     |     |       |      |        | ⭕    |
    language_boost          |     |     |      |     |     |       |      |        | ⭕    | ⭕
    params                  | ⭕  | ⭕  | ⭕   | ⭕  | ⭕  | ⭕    | ⭕   | ⭕    | ⭕    | ⭕
    """

    # ── 必填字段 ──
    task_type: TaskType
    prompt: str = ""

    # ── 负面提示词 ──
    negative_prompt: str = ""

    # ── 图片输入(图生图/编辑/图生视频) ──
    images: list[bytes] = field(default_factory=list)

    # ── 视频参考(Seedance 多模态) ──
    video_refs: list[MediaRef] = field(default_factory=list)

    # ── 音频参考(Seedance 多模态 / 语音克隆) ──
    audio_refs: list[MediaRef] = field(default_factory=list)

    # ── 有序多模态内容(Seedance 风格,严格按顺序) ──
    #    当该字段非空时,Adapter 应优先使用它而不是 prompt/images/video_refs/audio_refs
    ordered_content: list[ContentItem] = field(default_factory=list)

    # ── 音频输入(语音克隆参考音色,向后兼容字段) ──
    reference_audio: Optional[bytes] = None

    # ── ASR 输入:待转写音频(语义上与参考音频不同,但同样为单条 bytes) ──
    #    与 reference_audio 二选一:若两者都给,ASR 优先用 audio,reference_audio 给 TTS 复用。
    audio_payload: Optional[bytes] = None

    # ── 语音情绪(语音生成专用) ──
    mood: Optional[str] = None

    # ── 语音:音色 ID ──
    voice_id: Optional[str] = None

    # ── 语音:语速(0.5~2.0) ──
    speed: float = 1.0

    # ── 语音:语言增强 ──
    language_boost: str = "auto"

    # ── 尺寸参数 ──
    width: int = 720
    height: int = 1280

    # ── 视频宽高比(如 "16:9", "9:16", "adaptive") ──
    ratio: Optional[str] = None

    # ── 视频分辨率(480p/720p/1080p) ──
    resolution: Optional[str] = None

    # ── 视频时长(秒) ──
    duration: int = 5

    # ── Seedance 2.5 官方任务类型引导(与 duration 同级): auto / edit / extend ──
    omni_reference_task_type: Optional[str] = None

    # ── 随机种子 ──
    seed: Optional[int] = None

    # ── 是否生成有声视频(Seedance 等) ──
    # 默认开启;调用方显式传值即覆盖。不支持有声的模型在 normalize 中静默置 False。
    generate_audio: bool = True

    # ── 水印 ──
    # 默认关:与各视频模型 input_schema 的 watermark PortSpec(default=False)对齐。
    # PortSpec.default 只是给调用方展示用,服务端从不据它回填;省略该字段时真正生效的是
    # 本 dataclass 默认。之前默认 True 会让"调用方没传水印开关"(可选参数被
    # exclude_none 剔除)的请求静默出带水印视频。调用方显式传 True 才加水印。
    watermark: bool = False

    # ── 摄像机是否固定 ──
    camera_fixed: bool = False

    # ── 是否同时返回尾帧图 ──
    return_last_frame: bool = False

    # ── 推理档位(default/flex) ──
    service_tier: str = "default"

    # ── 模型覆盖(用户显式指定 Pipeline 名) ──
    model: Optional[str] = None

    # ── 通用任务级参数(替代旧的 extra:dict) ──
    #    约定:
    #      - 与 Seedance/MiniMax/MiMo 等 API 直接对应的字段应优先提升为命名字段
    #      - 只有真正后端私有、且无法通用化的字段才放进这里
    #      - 推荐使用 TypedAdapter 实现类型安全,而不是往这里堆 dict
    params: dict[str, Any] = field(default_factory=dict)

    # ── 兼容旧字段(已标记 deprecated,新代码不要使用) ──
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    # ── 上下文(可选,由调用方注入) ──
    user_id: Optional[str] = None
    trace_id: Optional[str] = None

    # ══════════════════════════════════════════════════════════════
    #  便捷属性
    # ══════════════════════════════════════════════════════════════

    @property
    def output_type(self) -> OutputType:
        """根据 task_type 推断输出类型"""
        return TASK_OUTPUT_MAP[self.task_type]

    @property
    def mime_type(self) -> str:
        """根据 task_type 推断 MIME 类型"""
        return TASK_MIME_MAP[self.task_type]

    @property
    def display_name(self) -> str:
        """根据 task_type 获取中文显示名"""
        return TASK_DISPLAY_NAME[self.task_type]

    # ── 向后兼容:旧的 extra 访问走 params/extra 合并 ──

    def get_param(self, key: str, default: Any = None) -> Any:
        """优先从 params 取,回退到 extra"""
        if key in self.params:
            return self.params[key]
        return self.extra.get(key, default)

    def set_param(self, key: str, value: Any) -> None:
        """写入到 params(不再写 extra)"""
        self.params[key] = value

    # ── 多模态内容便捷构造 ──

    def build_ordered_content(
        self,
        *,
        prepend_text: bool = True,
    ) -> list[ContentItem]:
        """根据 prompt/images/video_refs/audio_refs 构造有序 content

        当 ordered_content 已有值时直接返回;
        否则从通用字段构造一个合理的有序内容:
          [text(prompt), image(images[0]), image(images[1]), ...]
        """
        if self.ordered_content:
            return self.ordered_content

        # 延迟导入以避免循环:request ↔ types
        from .types import (
            image_ref,
            text_item,
            audio_item,
            image_item,
            video_item,
        )

        items: list[ContentItem] = []
        if prepend_text and self.prompt:
            items.append(text_item(self.prompt))
        for img in self.images:
            items.append(image_item(image_ref(data=img)))
        for v in self.video_refs:
            items.append(video_item(v))
        for a in self.audio_refs:
            items.append(audio_item(a))
        return items


# ═══════════════════════════════════════════════════════════════════════
#  GenerationResult
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class GenerationResult:
    """统一的 AIGC 生成结果

    简单场景下使用 (output_type, data, mime_type);
    复杂场景(Seedance 等)还可以使用 outputs/usage/last_frame_url 等扩展字段。
    """

    output_type: OutputType
    data: bytes
    mime_type: str = "image/png"
    model_used: str = ""
    pipeline_used: str = ""
    cost_points: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── 扩展字段(视频生成等使用) ──
    outputs: dict[str, Any] = field(default_factory=dict)  # e.g. {"video": bytes, "last_frame": bytes}
    usage: dict[str, Any] = field(default_factory=dict)  # e.g. {"completion_tokens": 246840}
    raw: dict[str, Any] = field(default_factory=dict)  # 厂商原始响应

    @classmethod
    def from_node_output(cls, node_output: Any) -> "GenerationResult":
        """从 NodeOutput 转换为 GenerationResult,在 request.py 中延迟
        引用 NodeOutput 类型,打破 types <-> request 的循环依赖。
        """
        return cls(
            output_type=OutputType(node_output.output_type),
            data=node_output.data,
            mime_type=node_output.mime_type,
            cost_points=int(node_output.usage.get("points", 0)),
            metadata=node_output.metadata,
        )
