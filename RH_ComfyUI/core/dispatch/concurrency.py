"""并发闸 — 全局兜底 Semaphore + 供应商(通道)级闸 + 可选模型级闸

分层(闸位不同):
  1. 全局闸(PLUGIN_CONFIG.Max_Concurrency,默认 600):dispatch 层总量兜底,
     防极端过载 —— generation_slot();
  2. 供应商闸(按选中通道的 channel.name 各一把):在 model.run() 选中通道后、
     execute_on_channel 前后包裹 —— channel_slot()。真正的供应商级隔离:
     同一模型的 ark / gateway / aifoundation / runninghub 通道互不挤占。
     RH 相关通道(runninghub / rh_app / comfyui,共用一块 GPU)取
     RH_Channel_Concurrency(默认 1),其余供应商取 Channel_Concurrency(默认 10);
  3. 模型闸(capabilities.max_concurrency,0=不限):稀缺本地资源的模型级串行。

上限热更新:每次取闸时都重读配置/类属性,数值变了就换一把新信号量。
已在旧信号量里执行的任务持有旧许可、自然完成;新任务立刻按新上限排队
(收缩上限时总并发会随旧任务完成逐渐收敛到新值,不会中断在跑任务)。
"""

from __future__ import annotations

import asyncio
from typing import Protocol, AsyncIterator
from contextlib import asynccontextmanager

from gsuid_core.logger import logger


class _GatedModel(Protocol):
    """并发闸需要的最小模型视图(避免 import AIGCGenerationBase 造成环)。"""

    name: str
    max_concurrency: int

_global_semaphore: asyncio.Semaphore | None = None
_global_limit: int = 0
_channel_semaphores: dict[str, tuple[asyncio.Semaphore, int]] = {}
_channel_inflight: dict[str, int] = {}
_model_semaphores: dict[str, tuple[asyncio.Semaphore, int]] = {}

# RunningHub 相关通道:runninghub(Seedance RH 通道)/ rh_app(RH 原生 App)/
# comfyui(RH 托管工作流)共用一块 GPU,必须低并发;其余通道是各自独立的远程 API。
_RH_CHANNELS = frozenset({"runninghub", "rh_app", "comfyui"})


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


def _channel_limit(key: str) -> int:
    """通道的并发上限:RH 相关一档,其余供应商一档(每次实时读配置)。"""
    if key in _RH_CHANNELS:
        return _read_int_config("RH_Channel_Concurrency", 1)
    return _read_int_config("Channel_Concurrency", 10)


def _get_channel_semaphore(channel_name: str) -> asyncio.Semaphore:
    """供应商(通道)级闸:每个 channel.name 独立一把,RH 相关与其他供应商分档取限。"""
    key = channel_name or "unknown"
    limit = _channel_limit(key)
    entry = _channel_semaphores.get(key)
    if entry is None or entry[1] != limit:
        sem = asyncio.Semaphore(limit)
        _channel_semaphores[key] = (sem, limit)
        logger.info(f"[Dispatch] 供应商通道 {key} 并发限制生效: {limit}")
        return sem
    return entry[0]


def _get_model_semaphore(model: _GatedModel) -> asyncio.Semaphore | None:
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
async def generation_slot(model: _GatedModel) -> AsyncIterator[None]:
    """dispatch 层:占用全局兜底闸与模型级闸(模型级默认无限)。

    供应商级限流不在这里 —— 通道要到 model.run() 里才选出来,
    由 channel_slot() 在 execute_on_channel 外围收口。
    """
    model_sem = _get_model_semaphore(model)
    async with _get_global_semaphore():
        if model_sem is None:
            yield
        else:
            async with model_sem:
                yield


@asynccontextmanager
async def channel_slot(channel_name: str) -> AsyncIterator[None]:
    """执行层:占用选中通道所属供应商的并发闸(model.run 内使用)。"""
    key = channel_name or "unknown"
    async with _get_channel_semaphore(key):
        _channel_inflight[key] = _channel_inflight.get(key, 0) + 1
        try:
            yield
        finally:
            _channel_inflight[key] = max(0, _channel_inflight.get(key, 1) - 1)


def channel_has_capacity(channel_name: str) -> bool:
    """通道当前是否还有空闲许可(尽力值,读后可能立刻变化,只用于软排序)。

    model.run() 用它把已满载的供应商排到候选末尾:ark 满 10 个在跑而
    gateway 空闲时,新任务先试 gateway,而不是阻塞在 ark 的信号量上。
    """
    key = channel_name or "unknown"
    return _channel_inflight.get(key, 0) < _channel_limit(key)


__all__ = ["generation_slot", "channel_slot", "channel_has_capacity"]
