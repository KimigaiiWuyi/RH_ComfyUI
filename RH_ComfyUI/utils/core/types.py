"""核心类型系统 — PortSpec / MediaRef / ContentItem / CapabilityManifest

为整个生成后端提供强类型抽象,取代之前的 `extra: dict` 逃生通道,
并为多模态参考(Seedance 2.0 等)提供有序内容序列。

类型设计要点:
1. 所有 enum 继承 str,运行时自动把 str 转为 enum(在 __post_init__ 中)
2. 提供便利构造器(text_item / image_item / video_item / audio_item / draft_task_item)
   避免每次都写 ContentItem(type=ContentItemType.TEXT, ...)
3. 严格类型注解:所有字段类型都是 enum / dataclass,不接受 Any
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Union, Callable, Optional, Awaitable
from dataclasses import field, dataclass

# ═══════════════════════════════════════════════════════════════════════
#  PortType — 端口类型枚举
# ═══════════════════════════════════════════════════════════════════════


class PortType(str, Enum):
    """端口数据类型"""

    # ── 基础类型 ──
    STRING = "string"  # 普通文本
    TEXT = "text"  # 同 STRING,语义上用于 prompt
    INTEGER = "integer"
    NUMBER = "number"  # float
    BOOLEAN = "boolean"
    ENUM = "enum"  # 取值受 values 约束
    LIST = "list"  # item_type 描述元素类型

    # ── 媒体类型(对应 MediaRef) ──
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"

    # ── 多模态有序内容(对应 list[ContentItem]) ──
    CONTENT = "content"  # 用于 Seedance 多模态参考

    # ── 输出 ──
    OUTPUT_IMAGE = "output_image"
    OUTPUT_VIDEO = "output_video"
    OUTPUT_AUDIO = "output_audio"


def _to_port_type(value: Union[PortType, str]) -> PortType:
    if isinstance(value, PortType):
        return value
    try:
        return PortType(value)
    except ValueError as exc:
        raise ValueError(f"未知 PortType: {value!r}") from exc


# ═══════════════════════════════════════════════════════════════════════
#  PortSpec — 端口规格声明
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class PortSpec:
    """单个输入/输出端口的声明式规格

    用于在 YAML 中声明节点端口的类型、约束与默认值。
    Adapter 在收到输入后会根据 PortSpec 进行校验与转换。

    title 与 description 面向不同读者:
    - title: 配置面板的短标题(几个字,无括号补充说明)
    - description: 完整语义说明,Agent/LLM 依赖它理解端口用法;
      缺 title 时调用方回退用 description 当标题
    """

    type: PortType
    required: bool = False
    default: Any = None
    description: str = ""
    title: str = ""

    # 数值约束
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    # 枚举取值(仅 ENUM 类型)
    values: Optional[list[Any]] = None

    # 列表元素类型(仅 LIST 类型)
    item_type: Optional[PortType] = None

    # 列表基数约束(仅 LIST 类型,用于输入档案路由:如首尾帧要求 >=2 张图)
    min_items: Optional[int] = None
    max_items: Optional[int] = None

    # 媒体类型白名单(IMAGE/VIDEO/AUDIO)
    mime_types: Optional[list[str]] = None

    def __post_init__(self) -> None:
        # 自动把 str 转为 PortType enum(运行时容错,但类型注解仍是 enum)
        self.type = _to_port_type(self.type)
        if self.item_type is not None:
            self.item_type = _to_port_type(self.item_type)

    # ── 序列化 ──

    @classmethod
    def from_dict(cls, data: Union[dict[str, Any], "PortSpec"]) -> "PortSpec":
        """从 dict 构造,便于 YAML 解析"""
        if isinstance(data, PortSpec):
            return data
        if not isinstance(data, dict):
            raise TypeError(f"PortSpec 需要 dict 或 PortSpec 实例,得到 {type(data)}")
        type_raw = data.get("type")
        if not type_raw:
            raise ValueError("PortSpec 缺少 type 字段")
        return cls(
            type=_to_port_type(type_raw),
            required=bool(data.get("required", False)),
            default=data.get("default"),
            description=data.get("description", ""),
            title=data.get("title", ""),
            minimum=data.get("minimum"),
            maximum=data.get("maximum"),
            values=data.get("values"),
            item_type=_to_port_type(data["item_type"]) if data.get("item_type") else None,
            min_items=data.get("min_items"),
            max_items=data.get("max_items"),
            mime_types=data.get("mime_types"),
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化"""
        out: dict[str, Any] = {"type": self.type.value}
        if self.required:
            out["required"] = True
        if self.default is not None:
            out["default"] = self.default
        if self.description:
            out["description"] = self.description
        if self.title:
            out["title"] = self.title
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.values is not None:
            out["values"] = list(self.values)
        if self.item_type is not None:
            out["item_type"] = self.item_type.value
        if self.min_items is not None:
            out["min_items"] = self.min_items
        if self.max_items is not None:
            out["max_items"] = self.max_items
        if self.mime_types is not None:
            out["mime_types"] = list(self.mime_types)
        return out


# ═══════════════════════════════════════════════════════════════════════
#  MediaRef — 多模态媒体引用
# ═══════════════════════════════════════════════════════════════════════


class MediaKind(str, Enum):
    """媒体类型"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


def _to_media_kind(value: Union[MediaKind, str]) -> MediaKind:
    if isinstance(value, MediaKind):
        return value
    try:
        return MediaKind(value)
    except ValueError as exc:
        raise ValueError(f"未知 MediaKind: {value!r}") from exc


def _guess_mime(data: bytes, kind: MediaKind) -> str:
    """根据文件头嗅探 mime_type"""
    if kind == MediaKind.IMAGE:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if data[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if data[:4] == b"GIF8":
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"
    if kind == MediaKind.VIDEO:
        if data[4:8] == b"ftyp":
            return "video/mp4"
        if data[:4] == b"\x00\x00\x00\x14ftyp" or b"ftyp" in data[:32]:
            return "video/mp4"
        return "video/mp4"
    if kind == MediaKind.AUDIO:
        if data[:4] == b"RIFF":
            return "audio/wav"
        if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
            return "audio/mpeg"
        return "audio/mpeg"
    return "application/octet-stream"


@dataclass
class MediaRef:
    """对一段媒体(图片/视频/音频)的引用

    至少提供 `data`(原始字节)或 `url`(公网 URL)之一。
    `role` 用于标识首帧(first_frame)、尾帧(last_frame)或一般参考(reference)。
    """

    kind: MediaKind
    data: Optional[bytes] = None
    url: Optional[str] = None
    role: Optional[str] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None

    def __post_init__(self) -> None:
        # 自动把 str 转为 MediaKind enum
        self.kind = _to_media_kind(self.kind)
        if self.data is None and self.url is None:
            raise ValueError("MediaRef 必须提供 data 或 url 之一")
        # 自动推断 mime_type
        if self.mime_type is None and self.data is not None:
            self.mime_type = _guess_mime(self.data, self.kind)

    def is_remote(self) -> bool:
        return self.url is not None and self.data is None


# ── MediaRef 便利构造器 ──


def image_ref(
    data: Optional[bytes] = None,
    *,
    url: Optional[str] = None,
    role: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> MediaRef:
    """构造图片引用"""
    return MediaRef(kind=MediaKind.IMAGE, data=data, url=url, role=role, mime_type=mime_type)


def video_ref(
    data: Optional[bytes] = None,
    *,
    url: Optional[str] = None,
    role: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> MediaRef:
    """构造视频引用"""
    return MediaRef(kind=MediaKind.VIDEO, data=data, url=url, role=role, mime_type=mime_type)


def audio_ref(
    data: Optional[bytes] = None,
    *,
    url: Optional[str] = None,
    role: Optional[str] = None,
    mime_type: Optional[str] = None,
) -> MediaRef:
    """构造音频引用"""
    return MediaRef(kind=MediaKind.AUDIO, data=data, url=url, role=role, mime_type=mime_type)


# ═══════════════════════════════════════════════════════════════════════
#  ContentItem — 有序多模态内容项(Seedance 风格)
# ═══════════════════════════════════════════════════════════════════════


class ContentItemType(str, Enum):
    """content 数组中元素的类型"""

    TEXT = "text"
    IMAGE = "image_url"
    VIDEO = "video_url"
    AUDIO = "audio_url"
    DRAFT_TASK = "draft_task"  # Seedance 样片复用


def _to_content_item_type(value: Union[ContentItemType, str]) -> ContentItemType:
    if isinstance(value, ContentItemType):
        return value
    try:
        return ContentItemType(value)
    except ValueError as exc:
        raise ValueError(f"未知 ContentItemType: {value!r}") from exc


@dataclass
class ContentItem:
    """content 数组中的单个元素,保留顺序

    Seedance 2.0 允许 text/image/video/audio 在数组中按任意顺序排列,
    并通过 `role` 字段指明语义(如 first_frame/last_frame)。
    例如 prompt 中提到 "图片1" 对应数组中 role=reference 的某个 image。
    """

    type: ContentItemType
    text: Optional[str] = None
    media: Optional[MediaRef] = None
    role: Optional[str] = None  # first_frame / last_frame / reference
    draft_task_id: Optional[str] = None  # 仅用于 DRAFT_TASK

    def __post_init__(self) -> None:
        # 自动把 str 转为 ContentItemType enum
        self.type = _to_content_item_type(self.type)

        if self.type == ContentItemType.TEXT and not self.text:
            raise ValueError("ContentItem(TEXT) 必须提供 text")
        if (
            self.type
            in (
                ContentItemType.IMAGE,
                ContentItemType.VIDEO,
                ContentItemType.AUDIO,
            )
            and self.media is None
        ):
            raise ValueError(f"ContentItem({self.type.value}) 必须提供 media")
        if self.type == ContentItemType.DRAFT_TASK and not self.draft_task_id:
            raise ValueError("ContentItem(DRAFT_TASK) 必须提供 draft_task_id")

    # ── 序列化(用于发往后端 API) ──

    def to_seedance_dict(self) -> dict[str, Any]:
        """序列化为 Seedance API 期望的字典结构"""
        if self.type == ContentItemType.TEXT:
            return {"type": "text", "text": self.text}
        if self.type == ContentItemType.IMAGE:
            assert self.media is not None
            d: dict[str, Any] = {
                "type": "image_url",
                "image_url": {"url": self.media.url or ""},
            }
            if self.role:
                d["role"] = self.role
            return d
        if self.type == ContentItemType.VIDEO:
            assert self.media is not None
            d = {
                "type": "video_url",
                "video_url": {"url": self.media.url or ""},
            }
            if self.role:
                d["role"] = self.role
            return d
        if self.type == ContentItemType.AUDIO:
            assert self.media is not None
            d = {
                "type": "audio_url",
                "audio_url": {"url": self.media.url or ""},
            }
            if self.role:
                d["role"] = self.role
            return d
        if self.type == ContentItemType.DRAFT_TASK:
            return {
                "type": "draft_task",
                "draft_task": {"id": self.draft_task_id or ""},
            }
        raise ValueError(f"未知 ContentItemType: {self.type}")


# ── ContentItem 便利构造器(类型安全,无字符串字面量) ──


def text_item(text: str, *, role: Optional[str] = None) -> ContentItem:
    """构造文本 content 项"""
    return ContentItem(type=ContentItemType.TEXT, text=text, role=role)


def image_item(
    media: MediaRef,
    *,
    role: Optional[str] = None,
) -> ContentItem:
    """构造图片 content 项(media 必须是 MediaRef)"""
    return ContentItem(type=ContentItemType.IMAGE, media=media, role=role)


def video_item(
    media: MediaRef,
    *,
    role: Optional[str] = None,
) -> ContentItem:
    """构造视频 content 项"""
    return ContentItem(type=ContentItemType.VIDEO, media=media, role=role)


def audio_item(
    media: MediaRef,
    *,
    role: Optional[str] = None,
) -> ContentItem:
    """构造音频 content 项"""
    return ContentItem(type=ContentItemType.AUDIO, media=media, role=role)


def draft_task_item(draft_task_id: str) -> ContentItem:
    """构造基于 draft_task_id 的 content 项(Seedance 样片复用)"""
    return ContentItem(type=ContentItemType.DRAFT_TASK, draft_task_id=draft_task_id)


# ═══════════════════════════════════════════════════════════════════════
#  CapabilityManifest — 能力声明(驱动路由)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class CapabilityManifest:
    """Adapter / Node 的能力声明

    用于:
    - 路由阶段: 根据 task_type 过滤可用节点
    - UI 阶段: 根据 supported_params 显示对应参数面板
    - 智能推荐: 优先级与降级链
    """

    supported_tasks: list[str] = field(default_factory=list)  # TaskType 字符串列表
    supported_params: list[str] = field(default_factory=list)  # 该 Adapter 实际消费的字段
    output_mime: list[str] = field(default_factory=list)  # 可能输出的 mime

    # 异步模式: sync / async_poll / async_callback
    mode: str = "sync"

    # 模型级并发上限(0=不限,只受全局闸约束)。只有稀缺的本地资源才需要它:
    # ComfyUI 共用一块 GPU 故声明 1;远程 API 是网络等待,留 0。
    max_concurrency: int = 0

    # 路由相关
    priority: int = 50  # 数字越大越优先,默认中等
    fallback: Optional[str] = None  # 不可用时回退的节点名

    # 健康相关
    health_status: str = "unknown"  # ok / degraded / down / unknown
    avg_latency_seconds: float = 0.0  # 用于负载均衡


# ═══════════════════════════════════════════════════════════════════════
#  ProgressEvent — 统一进度事件
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ProgressEvent:
    """Adapter 上报的统一进度事件

    由 Adapter 在长任务(尤其视频生成)轮询过程中上报,
    由 Dispatcher 透传给上层(命令/UI/Webhook)。
    """

    stage: str  # queued / running / uploading / downloading ...
    percent: float = 0.0  # 0~100
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# 进度回调:async 回调,全项目唯一定义(其余模块一律 re-import 此符号)
ProgressCallback = Callable[[ProgressEvent], Awaitable[Optional[None]]]


# ═══════════════════════════════════════════════════════════════════════
#  NodeOutput — 统一节点输出
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class NodeOutput:
    """Adapter 执行后返回的统一输出

    取代旧的 GenerationResult,新增 usage/raw/last_frame_url 等扩展字段,
    同时把 outputs 字典化以适配多模态输出(视频+尾帧图)。
    """

    status: str = "ok"  # ok / failed
    output_type: str = "image"  # image / video / audio
    data: bytes = b""  # 主产物字节
    mime_type: str = "image/png"

    # 多输出场景(如视频生成同时返回尾帧图)
    outputs: dict[str, Any] = field(default_factory=dict)

    # 用量与计费
    usage: dict[str, Any] = field(default_factory=dict)

    # 诊断: 厂商原始响应
    raw: dict[str, Any] = field(default_factory=dict)

    # 元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── 便捷构造(向后兼容 GenerationResult) ──

    @classmethod
    def from_result(cls, result: Any) -> "NodeOutput":
        """从旧 GenerationResult 构造"""
        return cls(
            status="ok",
            output_type=result.output_type.value if hasattr(result.output_type, "value") else str(result.output_type),
            data=result.data,
            mime_type=result.mime_type,
            outputs={"main": result.data},
            usage={"points": result.cost_points},
            metadata=result.metadata,
        )

    def to_result(self) -> dict[str, Any]:
        """降级为字典形式,避免 utils.core.types 与 utils.core.request 之间的循环依赖。

        如需完整的 GenerationResult 对象,请使用
        utils.core.request.GenerationResult.from_node_output(node_output)。
        """
        return {
            "output_type": self.output_type,
            "data": self.data,
            "mime_type": self.mime_type,
            "cost_points": int(self.usage.get("points", 0)),
            "metadata": self.metadata,
        }


__all__ = [
    # 枚举
    "PortType",
    "MediaKind",
    "ContentItemType",
    # 类型
    "PortSpec",
    "MediaRef",
    "ContentItem",
    "CapabilityManifest",
    "ProgressEvent",
    "ProgressCallback",
    "NodeOutput",
    # 便利构造器
    "image_ref",
    "video_ref",
    "audio_ref",
    "text_item",
    "image_item",
    "video_item",
    "audio_item",
    "draft_task_item",
]
