"""RH_ComfyUI — AIGC 统一生成插件"""

from gsuid_core.sv import Plugins
from gsuid_core.server import on_core_start

Plugins(
    name="RH_ComfyUI",
    force_prefix=["rh", "cf", "RH"],
    allow_empty_prefix=False,
)

# 触发 rh_models 的 setup():挂载 /RH_ComfyUI/models 系列 FastAPI 路由 + 注册命令
# 其余业务子包在此显式装载(顺序确定),配合下方模块身份统一,
# 无论本树经哪条路径先被 import(嵌套加载器 / 跨插件 re-export / 测试),
# 加载器后续的 cached_import 都会直接复用,不再 exec 出第二棵模块树。
from . import (  # noqa: F401, E402
    core,
    utils,
    models,
    rh_help,
    rh_admin,
    rh_agent,
    rh_models,  # noqa: F401, E402
    rh_generate,
)

# ── 模块身份统一:三种 import 前缀共享同一棵模块树 ──
# (防止注册表 / 负载均衡熔断状态分裂、@on_core_start 钩子重复执行)
from .utils.module_identity import unify_module_identity  # noqa: E402

# 确保配置在初始化时被注册到 gsuid_core 网页控制台
from .rh_config.comfyui_config import PLUGIN_CONFIG, SERVICE_CONFIG  # noqa: F401, E402

unify_module_identity(__name__)


@on_core_start
async def init_pipeline_system() -> None:
    """启动时初始化 Pipeline 注册表和 Backend 注册表"""
    from .utils.backends import init_backends
    from .utils.core.pipeline import pipeline_registry

    # 1. 注册后端（返回 AdapterRegistry 实例）
    registry = init_backends()

    # 2. 装载模型注册表(全编程式:models/*/defs.py 的模型类 + entry points 外部模型)
    #    NodeDef 由各模型类的 node_def() 用代码构建,不再从 YAML 加载
    from .models import discover_builtin_models

    model_count = discover_builtin_models()

    # 3. 注册 AI 知识库
    from .rh_generate._knowledge import register_pipeline_knowledge

    register_pipeline_knowledge()

    from gsuid_core.logger import logger

    from .utils.database.models import RHComfyuiTaskRecord

    # 5. 触发统计模块加载(语法/import 错误在启动期立即暴露,优于运行时爆炸)
    from .utils.database.statistics import record_task

    _ = (record_task, RHComfyuiTaskRecord)

    logger.info(
        f"[RHComfyUI] 初始化完成: {len(pipeline_registry.all_pipelines())} 个 Pipeline, "
        f"{len(registry.all())} 个 Backend, {model_count} 个模型, 统计模块已就绪"
    )
