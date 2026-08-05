"""并发闸 — 两层(均按 channel 维度,无全局兜底)

  1. 供应商全局闸:key=channel.name,上限 Channel_Concurrency(默认 10)
     防同一供应商被多模型一起打爆。
     **例外**:RunningHub 相关通道(runninghub / rh_app / comfyui)共用同一把闸
     (key=`rh`),上限 RH_Channel_Concurrency(默认 1)。它们共用同一 RH 账户
     并发配额,上游超限返回 421 TASK_QUEUE_MAXED("请自行排队");本地必须
     串行化,让第 2 个请求排队等许可,而不是打到上游报错。
  2. (model, channel) 闸:key=(model.name, channel.name),
     上限 = min(通道基线, model.max_concurrency)
     防单一模型在单一 channel 上挤爆。
     - 5 个本地 ComfyUI 工作流声明 max_concurrency=1 → (model, channel) 闸 = 1
     - aigc_system 的 _AIFConcurrencyMixin 模型(AIF_Max_Concurrency 默认 3)→ 3
     - 其它模型(max_concurrency=0,不限)→ 退化为通道基线

两层嵌套使用:`model.run()` 内先拿供应商全局闸,再拿 (model, channel) 闸。
两把信号量独立,所以 (model_a, channel_x) 满载时,同一 channel_x 上的
(model_b, channel_x) 不受影响(只要供应商全局闸还有空)。

上限热更新:每次取闸都重读配置/属性,数值变了就换一把新信号量。
已在旧信号量里执行的任务持有旧许可、自然完成;新任务按新上限排队。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from gsuid_core.logger import logger


# 供应商全局闸(按 channel.name;RH 通道归一到共享 key)
_channel_semaphores: dict[str, tuple[asyncio.Semaphore, int]] = {}
_channel_inflight: dict[str, int] = {}

# (model, channel) 闸
_pair_semaphores: dict[tuple[str, str], tuple[asyncio.Semaphore, int]] = {}
_pair_inflight: dict[tuple[str, str], int] = {}

# RunningHub 相关通道:共用同一 RH 账户并发配额,必须共享一把闸。
# - runninghub: Seedance RH 通道
# - rh_app: RH 原生 AI App
# - comfyui: 本地/RH 代理工作流(RH 代理与网站任务共享并发)
_RH_CHANNELS = frozenset({"runninghub", "rh_app", "comfyui"})
_RH_SHARED_KEY = "rh"


def _read_int_config(key: str, default: int) -> int:
    from ...rh_config.comfyui_config import PLUGIN_CONFIG

    raw = PLUGIN_CONFIG.get_config(key).data
    if isinstance(raw, int) and raw >= 1:
        return raw
    return default


def _normalize_channel_key(channel_name: str) -> str:
    """RH 相关通道归一到共享 key,其余保持原名。"""
    key = channel_name or "unknown"
    if key in _RH_CHANNELS:
        return _RH_SHARED_KEY
    return key


def _channel_limit(channel_name: str) -> int:
    """通道并发上限:RH 共享池一档,其余供应商一档。"""
    if _normalize_channel_key(channel_name) == _RH_SHARED_KEY:
        return _read_int_config("RH_Channel_Concurrency", 1)
    return _read_int_config("Channel_Concurrency", 10)


def _channel_baseline() -> int:
    """非 RH 供应商闸基线(兼容旧调用/测试名)。"""
    return _read_int_config("Channel_Concurrency", 10)


def _pair_limit(model: object, channel_name: str = "") -> int:
    """(model, channel) 闸上限 = min(通道基线, model.max_concurrency)

    通道基线跟随该 channel 所属池(RH 用 RH_Channel_Concurrency,
    其它用 Channel_Concurrency),避免 pair 闸比供应商闸更宽。
    """
    base = _channel_limit(channel_name) if channel_name else _channel_baseline()
    model_cap = getattr(model, "max_concurrency", 0) or 0
    if model_cap <= 0:
        return base
    return min(base, model_cap)


def _get_channel_semaphore(channel_name: str) -> asyncio.Semaphore:
    """供应商全局闸:每个规范化 channel key 一把(RH 通道共享)。"""
    key = _normalize_channel_key(channel_name)
    limit = _channel_limit(channel_name)
    entry = _channel_semaphores.get(key)
    if entry is None or entry[1] != limit:
        sem = asyncio.Semaphore(limit)
        _channel_semaphores[key] = (sem, limit)
        logger.info(f"[Dispatch] 供应商 {key} 并发限制生效: {limit}")
        return sem
    return entry[0]


def _get_pair_semaphore(model_name: str, channel_name: str, limit: int) -> asyncio.Semaphore:
    """(model, channel) 闸:每个组合一把"""
    key = (model_name or "unknown", channel_name or "unknown")
    entry = _pair_semaphores.get(key)
    if entry is None or entry[1] != limit:
        sem = asyncio.Semaphore(limit)
        _pair_semaphores[key] = (sem, limit)
        logger.info(f"[Dispatch] 模型×供应商 {key[0]}/{key[1]} 并发限制生效: {limit}")
        return sem
    return entry[0]


@asynccontextmanager
async def channel_slot(channel_name: str) -> AsyncIterator[None]:
    """供应商全局闸:model.run() 拿第一层许可。

    防同一 channel 被所有模型一起打爆;RH 相关通道共享一把闸,
    默认 RH_Channel_Concurrency=1,第 2 个请求在此排队等待。
    """
    key = _normalize_channel_key(channel_name)
    async with _get_channel_semaphore(channel_name):
        _channel_inflight[key] = _channel_inflight.get(key, 0) + 1
        try:
            yield
        finally:
            _channel_inflight[key] = max(0, _channel_inflight.get(key, 1) - 1)


@asynccontextmanager
async def channel_slot_for_model(model: object, channel_name: str) -> AsyncIterator[None]:
    """(model, channel) 闸:model.run() 嵌套在 channel_slot 内拿第二层许可。

    上限 = min(通道基线, model.max_concurrency),
    让模型自带闸(本地 ComfyUI=1 / aifoundation 独有=3)在不引入额外配置的情况下生效。
    """
    model_name = str(getattr(model, "name", "unknown") or "unknown")
    key = (model_name, channel_name or "unknown")
    limit = _pair_limit(model, channel_name)
    async with _get_pair_semaphore(model_name, channel_name, limit):
        _pair_inflight[key] = _pair_inflight.get(key, 0) + 1
        try:
            yield
        finally:
            _pair_inflight[key] = max(0, _pair_inflight.get(key, 1) - 1)


def channel_has_capacity(channel_name: str) -> bool:
    """供应商全局闸软排序(多通道时把满载的排末尾,优先试空闲供应商)"""
    key = _normalize_channel_key(channel_name)
    return _channel_inflight.get(key, 0) < _channel_limit(channel_name)


__all__ = ["channel_slot", "channel_slot_for_model", "channel_has_capacity"]
