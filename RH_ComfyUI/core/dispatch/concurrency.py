"""并发闸 — 全局 Semaphore(平移自 utils/core/executor) + 可选模型级闸"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator
from contextlib import asynccontextmanager

from gsuid_core.logger import logger

if TYPE_CHECKING:
    from ..base.generation import AIGCGenerationBase

_global_semaphore: asyncio.Semaphore | None = None
_model_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_global_semaphore() -> asyncio.Semaphore:
    """全局并发限制(PLUGIN_CONFIG.Max_Concurrency,与旧 executor 语义一致)"""
    global _global_semaphore
    if _global_semaphore is None:
        from ...rh_config.comfyui_config import PLUGIN_CONFIG

        concurrency = PLUGIN_CONFIG.get_config("Max_Concurrency").data
        if not isinstance(concurrency, int) or concurrency < 1:
            concurrency = 1
        _global_semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[Dispatch] 全局并发限制初始化: {concurrency}")
    return _global_semaphore


def _get_model_semaphore(model: "AIGCGenerationBase") -> asyncio.Semaphore | None:
    if model.max_concurrency <= 0:
        return None
    sem = _model_semaphores.get(model.name)
    if sem is None:
        sem = asyncio.Semaphore(model.max_concurrency)
        _model_semaphores[model.name] = sem
    return sem


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
