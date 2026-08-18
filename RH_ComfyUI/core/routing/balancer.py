"""LoadBalancer — 通用负载均衡 + 熔断

全模态统一的通道级负载均衡:
- 状态按 (scope, member) 二级 key 记在 LoadBalancer 实例属性上;
  scope=模型名,member=通道名;同一模型的多个通道共享一套熔断计数;
- 策略与阈值读 PLUGIN_CONFIG 通用键 Load_Balance_Mode / Failure_Threshold。
"""

from __future__ import annotations

import time
import random
import threading
from typing import Union, Callable, Optional
from dataclasses import dataclass

from gsuid_core.logger import logger

from ..channels.channel import ChannelBinding


@dataclass
class BalancerConfig:
    mode: str = "round_robin"  # round_robin / weighted / least_failures
    failure_threshold: int = 3  # 连续失败 N 次后熔断;0=不熔断
    circuit_breaker_seconds: float = 120.0


class LoadBalancer:
    """按 scope 隔离的负载均衡器;线程安全

    config_resolver(可选)在每次决策时被调用以获取最新配置 ——
    全局单例用它对接 PLUGIN_CONFIG,保证网页控制台改
    Load_Balance_Mode / Failure_Threshold 后不重启即生效。
    """

    def __init__(
        self,
        config: Optional[BalancerConfig] = None,
        *,
        config_resolver: Optional[Callable[[], BalancerConfig]] = None,
    ) -> None:
        self._config = config or BalancerConfig()
        self._config_resolver = config_resolver
        self._lock = threading.Lock()
        self._rr_index: dict[str, int] = {}
        self._failures: dict[tuple[str, str], int] = {}
        self._breaker_until: dict[tuple[str, str], float] = {}

    @property
    def config(self) -> BalancerConfig:
        if self._config_resolver is not None:
            try:
                return self._config_resolver()
            except Exception:  # noqa: BLE001 — 配置读取失败回落静态默认
                return self._config
        return self._config

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
        mode = self.config.mode

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
        config = self.config
        threshold = config.failure_threshold
        if threshold <= 0:
            return
        with self._lock:
            count = self._failures.get((scope, member), 0) + 1
            self._failures[(scope, member)] = count
            if count >= threshold:
                until = time.time() + config.circuit_breaker_seconds
                self._breaker_until[(scope, member)] = until
                logger.warning(
                    f"[Balancer] {scope}/{member} 连续失败 {count} 次,熔断 {config.circuit_breaker_seconds:.0f}s"
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


def _resolve_plugin_config() -> BalancerConfig:
    """每次决策时从 PLUGIN_CONFIG 读最新策略/阈值(网页控制台改完即生效)"""
    mode = "round_robin"
    threshold = 3
    from ...rh_config.comfyui_config import PLUGIN_CONFIG

    raw_mode = PLUGIN_CONFIG.get_config("Load_Balance_Mode").data
    if isinstance(raw_mode, str) and raw_mode:
        mode = raw_mode
    raw_threshold = PLUGIN_CONFIG.get_config("Failure_Threshold").data
    if raw_threshold is not None:
        try:
            threshold = int(raw_threshold)
        except (TypeError, ValueError):
            pass
    return BalancerConfig(mode=mode, failure_threshold=threshold)


def get_default_balancer() -> LoadBalancer:
    """全局单例;策略/阈值经 config_resolver 每次实时读 PLUGIN_CONFIG
    (配置键不存在时——如测试环境——resolver 抛 KeyError 回落内置默认)"""
    global _default_balancer
    if _default_balancer is None:
        _default_balancer = LoadBalancer(config_resolver=_resolve_plugin_config)
    return _default_balancer


__all__ = ["LoadBalancer", "BalancerConfig", "get_default_balancer"]
