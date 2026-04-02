"""模型类型定义"""

from enum import Enum, auto
from typing import List, Literal, Callable
from dataclasses import dataclass


class ModelRequirement(Enum):
    """模型依赖类型"""

    BLT_API = auto()  # 需要 BLT API Key
    COMFYUI_URL = auto()  # 需要 ComfyUI 服务地址
    RH_API = auto()  # 需要 RunningHub API Key


class ModelStatus(Enum):
    """模型可用状态"""

    AVAILABLE = "available"
    MISSING_BLT_API = "missing_blt_api"
    MISSING_COMFYUI = "missing_comfyui"
    MISSING_RH_API = "missing_rh_api"
    UNKNOWN = "unknown"


# 任务类型定义
TaskType = Literal[
    "text2image",
    "image2image",
    "image_edit",
    "text2video",
    "image2video",
    "music",
    "speech",
]


@dataclass
class ModelInfo:
    """模型信息"""

    name: str
    func: Callable
    requirements: List[ModelRequirement]
    task_type: TaskType
    description: str
    knowledge_content: str = ""  # 知识库内容，用于Agent选择参考


class ModelUnavailableError(Exception):
    """模型不可用异常"""

    def __init__(
        self,
        message: str,
        model_name: str = "",
        status: ModelStatus = ModelStatus.UNKNOWN,
    ):
        super().__init__(message)
        self.model_name = model_name
        self.status = status
        self.message = message
