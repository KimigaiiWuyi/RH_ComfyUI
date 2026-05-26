"""统一请求/响应模型 — 覆盖所有 AIGC 任务类型"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from dataclasses import field, dataclass


class TaskType(str, Enum):
    """任务类型枚举"""

    TEXT2IMAGE = "text2image"
    IMAGE2IMAGE = "image2image"
    IMAGE_EDIT = "image_edit"
    TEXT2VIDEO = "text2video"
    IMAGE2VIDEO = "image2video"
    MUSIC = "music"
    SPEECH = "speech"


class OutputType(str, Enum):
    """输出类型枚举"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


# 任务类型 → 输出类型 映射
TASK_OUTPUT_MAP: dict[TaskType, OutputType] = {
    TaskType.TEXT2IMAGE: OutputType.IMAGE,
    TaskType.IMAGE2IMAGE: OutputType.IMAGE,
    TaskType.IMAGE_EDIT: OutputType.IMAGE,
    TaskType.TEXT2VIDEO: OutputType.VIDEO,
    TaskType.IMAGE2VIDEO: OutputType.VIDEO,
    TaskType.MUSIC: OutputType.AUDIO,
    TaskType.SPEECH: OutputType.AUDIO,
}

# 任务类型 → MIME 类型 映射
TASK_MIME_MAP: dict[TaskType, str] = {
    TaskType.TEXT2IMAGE: "image/png",
    TaskType.IMAGE2IMAGE: "image/png",
    TaskType.IMAGE_EDIT: "image/png",
    TaskType.TEXT2VIDEO: "video/mp4",
    TaskType.IMAGE2VIDEO: "video/mp4",
    TaskType.MUSIC: "audio/mpeg",
    TaskType.SPEECH: "audio/mpeg",
}

# 任务类型 → 中文名称 映射
TASK_DISPLAY_NAME: dict[TaskType, str] = {
    TaskType.TEXT2IMAGE: "文生图",
    TaskType.IMAGE2IMAGE: "图生图",
    TaskType.IMAGE_EDIT: "图片编辑",
    TaskType.TEXT2VIDEO: "文生视频",
    TaskType.IMAGE2VIDEO: "图生视频",
    TaskType.MUSIC: "音乐生成",
    TaskType.SPEECH: "语音生成",
}


@dataclass
class GenerationRequest:
    """统一的 AIGC 生成请求 — 覆盖所有任务类型

    字段使用矩阵:
        字段              | text2image | image2image | image_edit | text2video | image2video | music | speech
        prompt            | ✅ 必须    | ✅ 必须     | ✅ 必须    | ✅ 必须    | ✅ 必须     | ✅ 风格 | ✅ 文本
        images            | ❌         | ✅ 1张      | ✅ 1~N张   | ❌         | ✅ 1张      | ❌     | ❌
        reference_audio   | ❌         | ❌          | ❌         | ❌         | ❌          | ❌     | ⭕ 可选
        width             | ✅         | ✅          | ❌         | ✅         | ✅          | ❌     | ❌
        height            | ✅         | ✅          | ❌         | ✅         | ✅          | ❌     | ❌
        duration          | ❌         | ❌          | ❌         | ✅         | ✅          | ❌     | ❌
        negative_prompt   | ⭕         | ⭕          | ❌         | ⭕         | ⭕          | ❌     | ❌
        model             | ⭕         | ⭕          | ⭕         | ⭕         | ⭕          | ⭕     | ⭕
        extra             | ⭕         | ⭕          | ⭕         | ⭕         | ⭕          | ⭕     | ⭕
    """

    # ── 必填字段 ──
    task_type: TaskType
    prompt: str

    # ── 图片输入（图生图/编辑/图生视频） ──
    images: list[bytes] = field(default_factory=list)

    # ── 音频输入（语音克隆参考音色） ──
    reference_audio: Optional[bytes] = None

    # ── 尺寸参数 ──
    width: int = 720
    height: int = 1280

    # ── 视频时长（秒） ──
    duration: int = 5

    # ── 负面提示词 ──
    negative_prompt: str = ""

    # ── 模型覆盖（用户显式指定 Pipeline 名） ──
    model: Optional[str] = None

    # ── 扩展参数（后端专属参数） ──
    extra: dict = field(default_factory=dict)

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


@dataclass
class GenerationResult:
    """统一的 AIGC 生成结果"""

    output_type: OutputType
    data: bytes
    mime_type: str = "image/png"
    model_used: str = ""
    pipeline_used: str = ""
    cost_points: int = 0
    metadata: dict = field(default_factory=dict)
