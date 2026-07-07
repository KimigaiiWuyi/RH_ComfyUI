"""LoadBalancer — 通用负载均衡 + 熔断(自 backends/seedance/registry.py 提炼泛化)

变化点(相对 seedance 版):
- 状态从模块级全局字典 → LoadBalancer 实例属性,按 (scope, member) 二级 key
- scope 通常是模型名;同一模型的多个通道共享一套熔断计数
- 策略与阈值读 PLUGIN_CONFIG 通用键,Seedance_* 旧键迁移期兜底
"""

from __future__ import annotations

import time
import random
import threading
from typing import Union, Optional
from dataclasses import dataclass

from gsuid_core.logger import logger

from ..channels.channel import ChannelBinding


@dataclass
class BalancerConfig:
    mode: str = "round_robin"  # round_robin / weighted / least_failures
    failure_threshold: int = 3  # 连续失败 N 次后熔断;0=不熔断
    circuit_breaker_seconds: float = 120.0


class LoadBalancer:
    """按 scope 隔离的负载均衡器;线程安全"""

    def __init__(self, config: Optional[BalancerConfig] = None) -> None:
        self._config = config or BalancerConfig()
        self._lock = threading.Lock()
        self._rr_index: dict[str, int] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._breaker_until: dict[tuple[str, str], float] = {}

    def order_candidates(
        self,
        *,
        scope: str,
        candidates: list[ChannelBinding],
    ) -> list[ChannelBinding]:
        """返回按策略排序的候选;熔断中的排到末尾(而非剔除,保证兜底)"""
        if len(candidates) <= 1:
            return list(candidates)

        now = time.time()
        with self._lock:
            healthy = [c for c in candidates if now >= self._breaker_until.get((scope, c.channel.name), 0)]
        broken = [c for c in candidates if c not in healthy]

        pool = healthy if healthy else list(candidates)
        mode = self._config.mode

        if mode == "round_robin":
            with self._lock:
                idx = self._rr_index.get(scope, 0) % len(pool)
                self._rr_index[scope] = idx + 1
            ordered = pool[idx:] + pool[:idx]
        elif mode == "weighted":
            weighted: list[ChannelBinding] = []
            for c in pool:
                weighted.extend([c] * max(c.channel.weight, 1))
            first = random.choice(weighted)
            ordered = [first] + [c for c in pool if c is not first]
        elif mode == "least_failures":
            with self._lock:
                ordered = sorted(pool, key=lambda c: self._failures.get((scope, c.channel.name), 0))
        else:
            ordered = pool

        # 熔断中的通道保底追加在最后(全灭时仍有机会自然解封)
        return ordered + [c for c in broken if c not in ordered]

    def record_success(self, *, scope: str, member: str) -> None:
        with self._lock:
            self._failures.pop((scope, member), None)
            self._breaker_until.pop((scope, member), None)

    def record_failure(self, *, scope: str, member: str) -> None:
        threshold = self._config.failure_threshold
        if threshold <= 0:
            return
        with self._lock:
            count = self._failures.get((scope, member), 0) + 1
            self._failures[(scope, member)] = count
            if count >= threshold:
                until = time.time() + self._config.circuit_breaker_seconds
                self._breaker_until[(scope, member)] = until
                logger.warning(
                    f"[Balancer] {scope}/{member} 连续失败 {count} 次,熔断 {self._config.circuit_breaker_seconds:.0f}s"
                )

    def health_snapshot(self) -> dict[str, dict[str, Union[float, int, bool]]]:
        """健康快照(供 /models/summary 与调试命令)"""
        now = time.time()
        with self._lock:
            keys = set(self._failures) | set(self._breaker_until)
            return {
                f"{scope}/{member}": {
                    "failure_count": self._failures.get((scope, member), 0),
                    "circuit_open": now < self._breaker_until.get((scope, member), 0),
                }
                for (scope, member) in keys
            }


_default_balancer: Optional[LoadBalancer] = None


def get_default_balancer() -> LoadBalancer:
    """全局单例;配置从 PLUGIN_CONFIG 读取(懒加载,读不到用默认值)"""
    global _default_balancer
    if _default_balancer is None:
        mode = "round_robin"
        threshold = 3
        try:
            from ...rh_config.comfyui_config import SERVICE_CONFIG

            raw_mode = SERVICE_CONFIG.get_config("Seedance_Load_Balance").data
            if isinstance(raw_mode, str) and raw_mode:
                mode = raw_mode
            raw_threshold = SERVICE_CONFIG.get_config("Seedance_Failure_Threshold").data
            if isinstance(raw_threshold, int):
                threshold = raw_threshold
        except KeyError:
            # 配置键不存在(如测试环境):用内置默认策略
            pass
        _default_balancer = LoadBalancer(BalancerConfig(mode=mode, failure_threshold=threshold))
    return _default_balancer


__all__ = ["LoadBalancer", "BalancerConfig", "get_default_balancer"]
