"""RH_ComfyUI — AIGC 统一生成插件"""

from gsuid_core.sv import Plugins
from gsuid_core.server import on_core_start

Plugins(
    name="RH_ComfyUI",
    force_prefix=["rh", "cf", "RH"],
    allow_empty_prefix=False,
)

# 触发 rh_models 的 setup():挂载 /RH_ComfyUI/models 系列 FastAPI 路由 + 注册命令
from . import rh_models  # noqa: F401, E402

# 确保配置在初始化时被注册到 gsuid_core 网页控制台
from .rh_config.comfyui_config import PLUGIN_CONFIG, SERVICE_CONFIG  # noqa: F401, E402


@on_core_start
async def init_pipeline_system() -> None:
    """启动时初始化 Pipeline 注册表和 Backend 注册表"""
    from .utils.backends import init_backends
    from .utils.core.pipeline import pipeline_registry
    from .utils.resource.RESOURCE_PATH import PIPELINES_PATH, _CP_PIPELINES_PATH

    # 1. 注册后端（返回 AdapterRegistry 实例）
    registry = init_backends()

    # 2. 加载 Pipeline 定义（先从内置路径，再从运行时路径）
    if _CP_PIPELINES_PATH.exists():
        pipeline_registry.load_from_directory(_CP_PIPELINES_PATH)
    pipeline_registry.load_from_directory(PIPELINES_PATH)

    # 3. 注册 AI 知识库
    from .rh_generate._knowledge import register_pipeline_knowledge

    register_pipeline_knowledge()

    from gsuid_core.logger import logger

    from .utils.database.models import RHComfyuiTaskRecord

    # 4. 触发统计模块加载(语法/import 错误在启动期立即暴露,优于运行时爆炸)
    from .utils.database.statistics import record_task

    _ = (record_task, RHComfyuiTaskRecord)

    # AdapterRegistry 未暴露公开长度接口,这里使用 .backends 公开 dict(若存在),
    # 否则仅打印 Pipeline 数。两者都依赖于 registry 的内部状态,但由于运行时安全,
    # 使用 getattr 避免静态分析报告未知属性。
    backend_count = len(getattr(registry, "backends", {}) or getattr(registry, "_backends", {}))
    logger.info(
        f"[RHComfyUI] 初始化完成: {len(pipeline_registry.all_pipelines())} 个 Pipeline, "
        f"{backend_count} 个 Backend, 统计模块已就绪"
    )
