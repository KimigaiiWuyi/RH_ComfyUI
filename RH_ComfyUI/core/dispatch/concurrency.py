"""并发闸 — 全局兜底 Semaphore + 后端/供应商级闸 + 可选模型级闸

三层嵌套(generation_slot):
  1. 全局闸(PLUGIN_CONFIG.Max_Concurrency,默认 600):仅作总量兜底防极端过载;
  2. 后端闸(按 NodeDef.backend 各一把):RH 相关后端(rh_app/comfyui,共用一块
     GPU)取 RH_Backend_Concurrency(默认 1),其余供应商(seedance/fishaudio/
     minimax 等)各自取 Backend_Concurrency(默认 10),互不挤占;
  3. 模型闸(capabilities.max_concurrency,0=不限):稀缺本地资源的模型级串行。

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
_backend_semaphores: dict[str, tuple[asyncio.Semaphore, int]] = {}
_model_semaphores: dict[str, tuple[asyncio.Semaphore, int]] = {}

# RunningHub 相关后端:rh_app(RH 原生 App)与 comfyui(RH 托管工作流)共用
# 一块 GPU,必须低并发;其余后端是各自独立的远程 API。
_RH_BACKENDS = frozenset({"rh_app", "comfyui"})


def _read_int_config(key: str, default: int) -> int:
    from ...rh_config.comfyui_config import PLUGIN_CONFIG

    raw = PLUGIN_CONFIG.get_config(key).data
    if isinstance(raw, int) and raw >= 1:
        return raw
    return default


def _get_global_semaphore() -> asyncio.Semaphore:
    """全局并发兜底(PLUGIN_CONFIG.Max_Concurrency,改配置即刻生效)"""
    global _global_semaphore, _global_limit

    concurrency = _read_int_config("Max_Concurrency", 1)
    if _global_semaphore is None or concurrency != _global_limit:
        _global_semaphore = asyncio.Semaphore(concurrency)
        _global_limit = concurrency
        logger.info(f"[Dispatch] 全局并发限制生效: {concurrency}")
    return _global_semaphore


def _backend_of(model: "AIGCGenerationBase") -> str:
    """模型所属后端名(NodeDef.backend);纯编程式模型(node=None)按模型名各一把闸。"""
    return str(model.node.backend) if model.node is not None else model.name


def _get_backend_semaphore(model: "AIGCGenerationBase") -> asyncio.Semaphore:
    """后端/供应商级闸:RH 相关后端一档,其余后端一档,各后端独立一把。"""
    backend = _backend_of(model)
    if backend in _RH_BACKENDS:
        limit = _read_int_config("RH_Backend_Concurrency", 1)
    else:
        limit = _read_int_config("Backend_Concurrency", 10)
    entry = _backend_semaphores.get(backend)
    if entry is None or entry[1] != limit:
        sem = asyncio.Semaphore(limit)
        _backend_semaphores[backend] = (sem, limit)
        logger.info(f"[Dispatch] 后端 {backend} 并发限制生效: {limit}")
        return sem
    return entry[0]


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
    """同时占用全局兜底闸、后端闸与模型级闸(模型级默认无限)"""
    model_sem = _get_model_semaphore(model)
    async with _get_global_semaphore():
        async with _get_backend_semaphore(model):
            if model_sem is None:
                yield
            else:
                async with model_sem:
                    yield


__all__ = ["generation_slot"]
