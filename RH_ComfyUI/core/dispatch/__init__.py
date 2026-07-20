"""core.dispatch — 统一调度器(路由 → 计费 → 限流 → 执行 → 统计 → 退款)

并发闸两层(详见 concurrency.py):
  - 供应商全局闸(channel.name):每 channel 一把
  - (model, channel) 闸:每组合一把,上限 = min(基线, model.max_concurrency)
均封装在 model.run() 内,dispatcher 只负责超时预算 + 流程编排。
"""

from .context import DispatchContext
from .dispatcher import dispatch

__all__ = ["dispatch", "DispatchContext"]