"""统一执行器 — 根据 Pipeline 指定的后端分发执行"""

from __future__ import annotations

import time
import asyncio
from pathlib import Path

from gsuid_core.logger import logger

from .request import OutputType, GenerationResult, GenerationRequest
from .pipeline import PipelineDef

# 全局 Semaphore，懒加载（避免在导入时读取配置）
_generation_semaphore: asyncio.Semaphore | None = None

# 输出文件扩展名映射
_OUTPUT_EXTENSIONS: dict[OutputType, str] = {
    OutputType.IMAGE: ".png",
    OutputType.VIDEO: ".mp4",
    OutputType.AUDIO: ".mp3",
}


def _get_semaphore() -> asyncio.Semaphore:
    """获取全局并发控制 Semaphore（懒加载）"""
    global _generation_semaphore
    if _generation_semaphore is None:
        from ...rh_config.comfyui_config import RHCOMFYUI_CONFIG

        concurrency = RHCOMFYUI_CONFIG.get_config("Max_Concurrency").data
        if not isinstance(concurrency, int) or concurrency < 1:
            concurrency = 1
        _generation_semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[Executor] 全局并发限制初始化: {concurrency}")
    return _generation_semaphore


def _save_output(result: GenerationResult, task_type_str: str) -> Path:
    """将生成结果保存到 OUTPUT_PATH

    Args:
        result: 生成结果
        task_type_str: 任务类型字符串（用于子目录）

    Returns:
        保存的文件路径
    """
    from ...utils.resource.RESOURCE_PATH import OUTPUT_PATH

    ext = _OUTPUT_EXTENSIONS.get(result.output_type, ".bin")
    sub_dir = OUTPUT_PATH / task_type_str
    sub_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{int(time.time() * 1000)}{ext}"
    file_path = sub_dir / filename
    file_path.write_bytes(result.data)
    logger.info(f"[Executor] 已保存生成结果: {file_path} ({len(result.data)} bytes)")
    return file_path


async def execute_generation(
    request: GenerationRequest,
    pipeline: PipelineDef,
) -> GenerationResult:
    """统一执行入口：根据 Pipeline 指定的后端，分发执行

    这是整个系统的唯一执行路径，命令和 AI 工具都走这里。
    受全局 Semaphore 限流控制，所有后端（RH原生/ComfyUI/BLT）共享同一并发限制。
    生成完成后自动保存到 OUTPUT_PATH。
    """
    from ..backends import backend_registry

    backend = backend_registry.get(pipeline.backend)
    if backend is None:
        raise RuntimeError(f"后端 {pipeline.backend} 未注册")

    sem = _get_semaphore()
    async with sem:
        logger.info(
            f"[Executor] 执行生成: task={request.task_type.value}, pipeline={pipeline.name}, backend={pipeline.backend}"
        )

        result = await backend.execute(request, pipeline)
        result.pipeline_used = pipeline.name
        result.model_used = pipeline.display_name
        result.cost_points = pipeline.point_cost

        # 保存生成结果到本地
        try:
            saved_path = _save_output(result, request.task_type.value)
            result.metadata["saved_path"] = str(saved_path)
        except Exception as e:
            logger.warning(f"[Executor] 保存生成结果失败（不影响返回）: {e}")

        return result
