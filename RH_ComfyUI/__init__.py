"""RH_ComfyUI — AIGC 统一生成插件"""

from gsuid_core.sv import Plugins
from gsuid_core.server import on_core_start

Plugins(
    name="RH_ComfyUI",
    force_prefix=["rh", "cf", "RH"],
    allow_empty_prefix=False,
)


@on_core_start
async def init_pipeline_system() -> None:
    """启动时初始化 Pipeline 注册表和 Backend 注册表"""
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.utils.core.pipeline import pipeline_registry
    from RH_ComfyUI.utils.resource.RESOURCE_PATH import PIPELINES_PATH, _CP_PIPELINES_PATH

    # 1. 注册后端
    init_backends()

    # 2. 加载 Pipeline 定义（先从内置路径，再从运行时路径）
    if _CP_PIPELINES_PATH.exists():
        pipeline_registry.load_from_directory(_CP_PIPELINES_PATH)
    pipeline_registry.load_from_directory(PIPELINES_PATH)

    # 3. 注册 AI 知识库
    from RH_ComfyUI.rh_generate._knowledge import register_pipeline_knowledge

    register_pipeline_knowledge()

    from gsuid_core.logger import logger

    logger.info(
        f"[RHComfyUI] 初始化完成: "
        f"{len(pipeline_registry.all_pipelines())} 个 Pipeline, "
        f"{len(init_backends()._backends)} 个 Backend"
    )
