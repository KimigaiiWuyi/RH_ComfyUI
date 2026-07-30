"""RH_ComfyUI 数据库模型与统计写入入口"""

from .models import RHBind, RHComfyuiTaskRecord, RHComfyuiTaskStatus
from .statistics import begin_task, record_task
from .consumption import (
    resolve_record_saved_file,
    build_admin_records_payload,
    build_record_detail_payload,
    build_user_consumption_payload,
    build_admin_consumption_payload,
)

__all__ = [
    "RHBind",
    "RHComfyuiTaskRecord",
    "RHComfyuiTaskStatus",
    "begin_task",
    "record_task",
    # 结构化查询入口(供 bot 命令文本格式化 / 外部 HTTP 共用)
    "build_user_consumption_payload",
    "build_admin_consumption_payload",
    "build_admin_records_payload",
    "build_record_detail_payload",
    "resolve_record_saved_file",
]
