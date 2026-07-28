"""RH_ComfyUI — 顶层便捷导入。

外层 `__init__.py` 通过从内层 re-export 公开 API,
让 `from RH_ComfyUI import submit, is_available, ...` 直接可用。

实际包代码在内层 `RH_ComfyUI/RH_ComfyUI/`,符合
gscore-plugin-development 的"嵌套加载"规范。

> 这一层主要是为了 **跨插件调用**(如 canvas_backend 在另一插件中
> 通过 `from RH_ComfyUI.api import submit` 调用真实生成)。
> gsuid_core 的插件加载器把内层注册为 `RH_ComfyUI.RH_ComfyUI`,而
> 不在 `sys.path` 暴露顶层 `RH_ComfyUI` 包;所以必须在此处显式
> 透出符号才能被外部 import。
"""

from __future__ import annotations

# 公开 Python API(供 canvas_backend 等外部插件使用)
from .RH_ComfyUI.api import (  # noqa: F401
    GenerationResult,
    submit,
    list_models,
    is_available,
    get_point_cost,
    get_model_input_schema,
    charge_points,
    refund_points,
    get_quota_status,
    force_refill_points,
    get_all_tier_quotas,
)
from .RH_ComfyUI.core.billing.points_api import PointsDeniedError  # noqa: F401
from .RH_ComfyUI.core.billing.points_api import force_refill_bot_pool  # noqa: F401
from .RH_ComfyUI.core.billing.points_api import set_vip_tier as set_points_vip_tier  # noqa: F401
from .RH_ComfyUI.core.billing.points_api import refill_buckets  # noqa: F401
from .RH_ComfyUI.utils.backends import (  # noqa: F401
    AdapterRegistry,
    init_backends,
    backend_registry,
)
from .RH_ComfyUI.utils.core.types import ProgressEvent  # noqa: F401

# 引擎域错误基类(供 canvas_backend 等调用方判型:域错误在 dispatcher 已带
# 成因链打过日志,上层只需一行,避免同一失败的 traceback 重复刷屏)
from .RH_ComfyUI.core.base.errors import GenerationError as RHGenerationError  # noqa: F401

# 触发内层 __init__.py(执行 Plugins(...) + 注册命令 + @on_core_start 钩子)
from .RH_ComfyUI.utils.core.pipeline import NodeDef, pipeline_registry  # noqa: F401

# RHBind 积分表(供 canvas_backend 的 credit_rh 跨插件调用,与 bot 命令共用同一笔账)
# RHComfyuiTaskRecord 消费记录表:HTTP 生成自管积分(charge_rh/refund_rh),不经引擎
# billing,失败退款后需由调用方 mark_last_failed_refunded 把消费记录标为已退款。
from .RH_ComfyUI.utils.database.models import RHBind, RHComfyuiTaskRecord  # noqa: F401

# 结构化查询入口(供 canvas_backend HTTP / 其它下游插件使用)
# 在此显式 re-export,让 `from RH_ComfyUI import build_*_payload` 直接可用,
# 外部消费者无需依赖 `RH_ComfyUI.utils.database.consumption` 这条隐式子包路径。
from .RH_ComfyUI.utils.database.consumption import (  # noqa: F401
    build_admin_records_payload,
    build_record_detail_payload,
    build_user_consumption_payload,
    build_admin_consumption_payload,
    resolve_record_saved_file,
)

__all__ = [
    # 公开 API
    "submit",
    "get_point_cost",
    "list_models",
    "is_available",
    "get_model_input_schema",
    "GenerationResult",
    "ProgressEvent",
    "RHGenerationError",
    # 三重余额
    "charge_points",
    "refund_points",
    "get_quota_status",
    "force_refill_points",
    "force_refill_bot_pool",
    "get_all_tier_quotas",
    "set_points_vip_tier",
    "refill_buckets",
    "PointsDeniedError",
    # 高级(允许需要写 Adapter / Pipeline 的下游插件访问内部注册表)
    "NodeDef",
    "pipeline_registry",
    "AdapterRegistry",
    "backend_registry",
    "init_backends",
    # 结构化查询入口(供 canvas_backend HTTP / 其它下游插件使用)
    "build_user_consumption_payload",
    "build_admin_consumption_payload",
    "build_admin_records_payload",
    "build_record_detail_payload",
    "resolve_record_saved_file",
    # 跨插件积分扣减(canvas_backend credit_rh 使用)
    "RHBind",
    "RHComfyuiTaskRecord",
]
