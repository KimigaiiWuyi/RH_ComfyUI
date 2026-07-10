"""并发闸 — 全局 Semaphore(平移自 utils/core/executor) + 可选模型级闸

上限热更新:每次取闸时都重读配置/类属性,数值变了就换一把新信号量。
已在旧信号量里执行的任务持有旧许可、自然完成;新任务立刻按新上限排队
(收缩上限时总并发会随旧任务完成逐渐收敛到新值,不会中断在跑任务)。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator
from contextlib import asynccontextmanager

from gsuid_core.logger import logger

if TYPE_CHECKING:
    from ..base.generation import AIGCGenerationBase

_global_semaphore: asyncio.Semaphore | None = None
_global_limit: int = 0
_model_semaphores: dict[str, tuple[asyncio.Semaphore, int]] = {}


def _get_global_semaphore() -> asyncio.Semaphore:
    """全局并发限制(PLUGIN_CONFIG.Max_Concurrency,改配置即刻生效)"""
    global _global_semaphore, _global_limit
    from ...rh_config.comfyui_config import PLUGIN_CONFIG

    concurrency = PLUGIN_CONFIG.get_config("Max_Concurrency").data
    if not isinstance(concurrency, int) or concurrency < 1:
        concurrency = 1
    if _global_semaphore is None or concurrency != _global_limit:
        _global_semaphore = asyncio.Semaphore(concurrency)
        _global_limit = concurrency
        logger.info(f"[Dispatch] 全局并发限制生效: {concurrency}")
    return _global_semaphore


def _get_model_semaphore(model: "AIGCGenerationBase") -> asyncio.Semaphore | None:
    if model.max_concurrency <= 0:
        _model_semaphores.pop(model.name, None)
        return None
    entry = _model_semaphores.get(model.name)
    if entry is None or entry[1] != model.max_concurrency:
        sem = asyncio.Semaphore(model.max_concurrency)
        _model_semaphores[model.name] = (sem, model.max_concurrency)
        return sem
    return entry[0]


@asynccontextmanager
async def generation_slot(model: "AIGCGenerationBase") -> AsyncIterator[None]:
    """同时占用全局闸与模型级闸(模型级默认无限)"""
    model_sem = _get_model_semaphore(model)
    async with _get_global_semaphore():
        if model_sem is None:
            yield
        else:
            async with model_sem:
                yield


__all__ = ["generation_slot"]
