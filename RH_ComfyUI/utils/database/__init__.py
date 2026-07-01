"""RH_ComfyUI 数据库模型与统计写入入口"""

from .models import RHBind, RHComfyuiTaskRecord, RHComfyuiTaskStatus
from .statistics import record_task
from .consumption import (
    build_admin_records_payload,
    build_user_consumption_payload,
    build_admin_consumption_payload,
)

__all__ = [
    "RHBind",
    "RHComfyuiTaskRecord",
    "RHComfyuiTaskStatus",
    "record_task",
    # 结构化查询入口(供 bot 命令文本格式化 / canvas_backend HTTP 共用)
    "build_user_consumption_payload",
    "build_admin_consumption_payload",
    "build_admin_records_payload",
]
