"""RH_ComfyUI — 顶层便捷导入。

外层 `__init__.py` 通过从内层 re-export 公开 API,
让 `from RH_ComfyUI import submit, is_available, ...` 直接可用。

实际包代码在内层 `RH_ComfyUI/RH_ComfyUI/`,符合
gscore-plugin-development 的"嵌套加载"规范。

> 这一层主要是为了 **跨插件调用**(其它插件通过
> `from RH_ComfyUI.api import submit` 调用生成能力)。
> gsuid_core 的插件加载器把内层注册为 `RH_ComfyUI.RH_ComfyUI`,而
> 不在 `sys.path` 暴露顶层 `RH_ComfyUI` 包;所以必须在此处显式
> 透出符号才能被外部 import。
"""

from __future__ import annotations

# 公开 Python API(供其它插件 / HTTP 宿主使用)
from .RH_ComfyUI.api import (  # noqa: F401
    GenerationResult,
    submit,
    list_models,
    resume_poll,
    is_available,
    charge_points,
    refund_points,
    get_point_cost,
    get_quota_status,
    cancel_generation,
    settle_model_cost,
    force_refill_points,
    get_all_tier_quotas,
    list_quota_statuses,
    get_model_input_schema,
    reconcile_seedance_usage_billing,
)
from .RH_ComfyUI.utils.backends import (  # noqa: F401
    AdapterRegistry,
    init_backends,
    backend_registry,
)

# 引擎域错误基类(供调用方判型:域错误在 dispatcher 已带成因链打过日志,
# 上层只需一行,避免同一失败的 traceback 重复刷屏)
from .RH_ComfyUI.core.base.errors import GenerationError as RHGenerationError  # noqa: F401
from .RH_ComfyUI.utils.core.types import ProgressEvent  # noqa: F401

# 触发内层 __init__.py(执行 Plugins(...) + 注册命令 + @on_core_start 钩子)
from .RH_ComfyUI.utils.core.pipeline import NodeDef, pipeline_registry  # noqa: F401

# RHBind 积分表 / 任务统计表(与 bot 命令共用;HTTP 入口可外部预扣后走 ExternalPrepaidPolicy)
from .RH_ComfyUI.utils.database.models import (  # noqa: F401
    RHBind,
    RHComfyuiStatsCache,
    RHComfyuiTaskRecord,
)
from .RH_ComfyUI.core.billing.points_api import (
    PointsDeniedError,  # noqa: F401
    WalletIntegrityError,  # noqa: F401
    WalletOperationCommand,  # noqa: F401
    WalletOperationConflict,  # noqa: F401
    set_vip_tier as set_points_vip_tier,  # noqa: F401
    refill_buckets,  # noqa: F401
    charge_points_once,  # noqa: F401
    refund_points_once,  # noqa: F401
    settle_points_once,  # noqa: F401
    get_wallet_operation,  # noqa: F401
    force_refill_bot_pool,  # noqa: F401
    charge_points_in_session,  # noqa: F401
    refund_points_in_session,  # noqa: F401
    settle_points_in_session,  # noqa: F401
    get_wallet_job_operations,  # noqa: F401
)

# 结构化查询入口:显式 re-export,外部无需依赖隐式子包路径
from .RH_ComfyUI.utils.database.consumption import (  # noqa: F401
    build_admin_daily_payload,
    resolve_record_saved_file,
    build_admin_records_payload,
    build_record_detail_payload,
    build_filter_options_payload,
    build_user_consumption_payload,
    build_admin_consumption_payload,
)

__all__ = [
    "WalletIntegrityError",
    "WalletOperationCommand",
    "WalletOperationConflict",
    "charge_points_once",
    "settle_points_once",
    "refund_points_once",
    "charge_points_in_session",
    "settle_points_in_session",
    "refund_points_in_session",
    "get_wallet_operation",
    "get_wallet_job_operations",
    # 公开 API
    "submit",
    "cancel_generation",
    "resume_poll",
    "settle_model_cost",
    "reconcile_seedance_usage_billing",
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
    "list_quota_statuses",
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
    # 结构化查询入口(HTTP 宿主 / 其它下游插件)
    "build_user_consumption_payload",
    "build_admin_consumption_payload",
    "build_admin_records_payload",
    "build_admin_daily_payload",
    "build_filter_options_payload",
    "build_record_detail_payload",
    "resolve_record_saved_file",
    # 跨插件积分扣减(宿主 credit 入口)
    "RHBind",
    "RHComfyuiStatsCache",
    "RHComfyuiTaskRecord",
]
