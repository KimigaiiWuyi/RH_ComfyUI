"""统一执行器 — 根据节点指定的 Adapter 分发执行

⚠ 旧入口,实际生产路径已统一走 core.dispatch.dispatch() → model.run(),
后者内含两层并发闸(供应商闸 + (model, channel) 闸,详见
core/dispatch/concurrency.py)。本模块保留供历史 Adapter 兼容,但
execute_generation() 已无生产调用点。

受 Channel_Concurrency 兜底限流,所有 Adapter 共享同一并发限制。
生成完成后自动落盘到 OUTPUT_PATH,并记录统计。
"""

from __future__ import annotations

import time
import asyncio
from typing import TYPE_CHECKING, Optional
from pathlib import Path

from gsuid_core.logger import logger

from .types import NodeOutput, ProgressEvent
from .request import OutputType, GenerationResult, GenerationRequest
from ..database.statistics import record_task

if TYPE_CHECKING:
    from .pipeline import NodeDef

# ⚠ 旧全局 Semaphore(懒加载),仅在 execute_generation 被调用时才会初始化。
# 当前生产路径不再走 execute_generation,此闸实际为死代码;保留是为兼容外部
# 旧 import。新并发闸由 core.dispatch.concurrency 提供。
_generation_semaphore: asyncio.Semaphore | None = None

# 输出文件扩展名映射
_OUTPUT_EXTENSIONS: dict[OutputType, str] = {
    OutputType.IMAGE: ".png",
    OutputType.VIDEO: ".mp4",
    OutputType.AUDIO: ".mp3",
}


def _get_semaphore() -> asyncio.Semaphore:
    """获取旧版全局并发控制 Semaphore(懒加载)。

    读 Channel_Concurrency(与新架构基线对齐);原 Max_Concurrency 已废弃。
    """
    global _generation_semaphore
    if _generation_semaphore is None:
        from ...rh_config.comfyui_config import PLUGIN_CONFIG

        concurrency = PLUGIN_CONFIG.get_config("Channel_Concurrency").data
        if not isinstance(concurrency, int) or concurrency < 1:
            concurrency = 1
        _generation_semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[Executor] 旧全局并发限制初始化: {concurrency}")
    return _generation_semaphore


def _guess_ext(data: bytes, fallback: str) -> str:
    """根据文件头嗅探扩展名(用于附加产物,如尾帧图)"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:8] == b"ftyp":
        return ".mp4"
    if data[:4] == b"RIFF":
        return ".wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return ".mp3"
    return fallback


def _save_output(result: NodeOutput, task_type_str: str, default_ext: str = ".bin") -> Path:
    """将生成结果保存到 OUTPUT_PATH

    约定:`result.data` 为主产物;`result.outputs` 只存放**附加**产物
    (如视频生成的尾帧图),不再重复存放主产物(避免双写)。

    Args:
        result: 节点输出
        task_type_str: 任务类型字符串(用于子目录)
        default_ext: 主产物扩展名兜底

    Returns:
        主产物的保存路径
    """
    from ...utils.resource.RESOURCE_PATH import OUTPUT_PATH

    ext = _OUTPUT_EXTENSIONS.get(OutputType(result.output_type), default_ext)
    sub_dir = OUTPUT_PATH / task_type_str
    sub_dir.mkdir(parents=True, exist_ok=True)

    ts = int(time.time() * 1000)
    file_path = sub_dir / f"{ts}{ext}"
    file_path.write_bytes(result.data)
    logger.info(f"[Executor] 已保存生成结果: {file_path} ({len(result.data)} bytes)")
    saved_files: list[str] = [str(file_path)]

    # 落盘附加产物(跳过与主产物同一份字节,扩展名按文件头嗅探)
    for name, payload in (result.outputs or {}).items():
        if not isinstance(payload, (bytes, bytearray)):
            continue
        if payload is result.data or bytes(payload) == result.data:
            continue
        suffix = name.replace("_", "-")
        extra_ext = _guess_ext(bytes(payload), ext)
        extra_path = sub_dir / f"{ts}_{suffix}{extra_ext}"
        try:
            extra_path.write_bytes(payload)
            saved_files.append(str(extra_path))
            logger.info(f"[Executor] 已保存附加输出 {name}: {extra_path}")
        except Exception as e:
            logger.warning(f"[Executor] 保存附加输出 {name} 失败: {e}")

    # 全部落盘路径写进 metadata,供 statistics.record_task 入库
    # (saved_path 只有主产物,附加产物如尾帧图会丢;两个调用点
    #  executor.execute_generation / telemetry.recorder 都吃这份)
    result.metadata["saved_files"] = saved_files

    return file_path


async def execute_generation(
    request: GenerationRequest,
    node: NodeDef,
    *,
    on_progress=None,
    bot_id: str = "",
    group_id: str = "",
) -> GenerationResult:
    """统一执行入口:根据节点指定的 Adapter,分发执行

    这是整个系统的唯一执行路径,命令和 AI 工具都走这里。
    受全局 Semaphore 限流控制,所有 Adapter 共享同一并发限制。
    生成完成后自动保存到 OUTPUT_PATH,并包装为 GenerationResult。

    Args:
        request: 统一请求
        node: 选中的节点定义
        on_progress: 可选的进度回调,透传给 Adapter
        bot_id: 触发者 Bot 平台(Event.bot_id),用于任务统计
        group_id: 触发者群号(Event.group_id),用于任务统计

    Returns:
        GenerationResult(向下兼容旧 API)
    """
    from ..backends import backend_registry

    adapter = backend_registry.get(node.backend)
    if adapter is None:
        raise RuntimeError(f"Adapter {node.backend} 未注册")

    sem = _get_semaphore()
    start_ts = time.monotonic()
    status = "ok"
    error_repr: Optional[str] = None
    result: Optional[GenerationResult] = None

    async with sem:
        logger.info(f"[Executor] 执行生成: task={request.task_type.value}, node={node.name}, backend={node.backend}")
        try:
            node_output: NodeOutput = await adapter.execute(request, node, on_progress=on_progress)
            node_output.metadata.setdefault("saved_path", "")

            # 落盘失败不影响主流程,统计仍可标为成功
            try:
                saved_path = _save_output(node_output, request.task_type.value)
                node_output.metadata["saved_path"] = str(saved_path)
            except Exception as e:
                logger.warning(f"[Executor] 保存生成结果失败(不影响返回): {e}")

            # 包装为旧 GenerationResult
            result = GenerationResult(
                output_type=OutputType(node_output.output_type),
                data=node_output.data,
                mime_type=node_output.mime_type,
                model_used=node.display_name,
                pipeline_used=node.name,
                cost_points=node.point_cost,
                metadata=node_output.metadata,
                outputs=node_output.outputs,
                usage=node_output.usage,
                raw=node_output.raw,
            )
            return result
        except Exception as e:
            status = "failed"
            error_repr = repr(e)
            raise
        finally:
            # 成功 / 失败两条路径都被 finally 覆盖
            # record_task() 内部 try/except 兜底,失败仅打日志
            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            await record_task(
                request=request,
                result=result,
                node=node,
                status=status,
                elapsed_ms=elapsed_ms,
                error=error_repr,
                bot_id=bot_id,
                group_id=group_id,
                trace_id=request.trace_id or "",
            )


__all__ = ["execute_generation", "ProgressEvent"]
