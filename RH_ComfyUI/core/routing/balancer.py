"""LoadBalancer — 通用负载均衡 + 熔断(健康状态机委托 core.channels.health)

全模态统一的通道级负载均衡:
- 排序只在这里做;熔断/探测/节流状态在 ``ChannelHealth``(两级 key:
  group=共享上游端点,member=(模型,通道));
- 策略与阈值每次决策实时读 PLUGIN_CONFIG(Load_Balance_Mode / Failure_Threshold /
  Circuit_Base_Cooldown / Circuit_Max_Cooldown)。
"""

from __future__ import annotations

import time
import random
import threading
from typing import Union, Callable, Optional
from dataclasses import dataclass

from ..channels.health import AttemptLease, CircuitState, ChannelHealth
from ..channels.channel import ChannelBinding, ProviderChannel
from ..channels.failure import FAILOVER_SAFE_PHASES, Phase, FailureKind


@dataclass
class BalancerConfig:
    mode: str = "round_robin"  # round_robin / weighted / least_failures
    failure_threshold: int = 3  # 连续失败 N 次后熔断;0=不熔断
    # 兼容字段:base_cooldown 未显式给时作为冷却基数
    circuit_breaker_seconds: float = 30.0
    base_cooldown: Optional[float] = None
    max_cooldown: float = 1800.0

    def cooldown_base(self) -> float:
        return self.base_cooldown if self.base_cooldown is not None else self.circuit_breaker_seconds


def _member_key(scope: str, member: str) -> tuple[str, str, str]:
    return ("member", scope, member)


def _group_key(group: str) -> tuple[str, str]:
    return ("group", group)


class LoadBalancer:
    """按 scope 隔离的负载均衡器;线程安全

    config_resolver(可选)在每次决策时被调用以获取最新配置 ——
    全局单例用它对接 PLUGIN_CONFIG,保证网页控制台改
    Load_Balance_Mode / Failure_Threshold / Circuit_* 后不重启即生效。
    """

    def __init__(
        self,
        config: Optional[BalancerConfig] = None,
        *,
        config_resolver: Optional[Callable[[], BalancerConfig]] = None,
        health: Optional[ChannelHealth] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._config = config or BalancerConfig()
        self._config_resolver = config_resolver
        self._lock = threading.Lock()
        self._rr_index: dict[str, int] = {}
        self._failures: dict[tuple[str, str], int] = {}  # least_failures 排序参考
        self._health = health or ChannelHealth(clock=clock or time.monotonic)

    @property
    def health(self) -> ChannelHealth:
        return self._health

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
        strict: bool = False,
    ) -> list[ChannelBinding]:
        """按健康分桶 + 策略排序;熔断中的排到末尾(而非剔除,保证兜底)。

        桶序:[HALF_OPEN 可探测(strict 时要求 last_phase 允许重发)]
        → [CLOSED 未节流,按策略] → [节流中] → [OPEN 按到期升序]
        → [HALF_OPEN 不可探测]。
        """
        if len(candidates) <= 1:
            return list(candidates)

        probeable: list[ChannelBinding] = []
        closed: list[ChannelBinding] = []
        throttled: list[ChannelBinding] = []
        open_later: list[tuple[float, ChannelBinding]] = []
        half_blocked: list[ChannelBinding] = []
        for c in candidates:
            ev = self._health.evaluate(
                member_key=_member_key(scope, c.channel.name),
                group_key=_group_key(c.channel.health_group()),
            )
            if ev.state is CircuitState.HALF_OPEN:
                if ev.probe_free and (not strict or ev.last_phase in FAILOVER_SAFE_PHASES):
                    probeable.append(c)
                else:
                    half_blocked.append(c)
            elif ev.state is CircuitState.OPEN:
                open_later.append((ev.open_until, c))
            elif ev.throttled:
                throttled.append(c)
            else:
                closed.append(c)

        ordered_closed = self._strategy_order(scope=scope, pool=closed)
        open_later.sort(key=lambda t: t[0])
        return probeable + ordered_closed + throttled + [c for _, c in open_later] + half_blocked

    def _strategy_order(self, *, scope: str, pool: list[ChannelBinding]) -> list[ChannelBinding]:
        if not pool:
            return pool
        mode = self.config.mode
        if mode == "round_robin":
            with self._lock:
                idx = self._rr_index.get(scope, 0) % len(pool)
                self._rr_index[scope] = idx + 1
            return pool[idx:] + pool[:idx]
        if mode == "weighted":
            weighted: list[ChannelBinding] = []
            for c in pool:
                weighted.extend([c] * max(c.channel.weight, 1))
            first = random.choice(weighted)
            return [first] + [c for c in pool if c is not first]
        if mode == "least_failures":
            with self._lock:
                return sorted(pool, key=lambda c: self._failures.get((scope, c.channel.name), 0))
        return pool

    def begin_attempt(self, *, scope: str, channel: ProviderChannel) -> AttemptLease | None:
        """占用本次尝试的探测令牌;HALF_OPEN 令牌已被占 → None(调用方跳过)。"""
        return self._health.begin_attempt(
            member_key=_member_key(scope, channel.name),
            group_key=_group_key(channel.health_group()),
        )

    def end_attempt(self, lease: AttemptLease | None) -> None:
        self._health.end_attempt(lease)

    def record_success(
        self,
        *,
        scope: str,
        member: str,
        group: Optional[str] = None,
        lease: AttemptLease | None = None,
    ) -> None:
        self._health.record_success(
            member_key=_member_key(scope, member),
            group_key=_group_key(group or member),
            lease=lease,
        )
        with self._lock:
            self._failures.pop((scope, member), None)

    def record_failure(
        self,
        *,
        scope: str,
        member: str,
        kind: Optional[FailureKind] = None,
        phase: Optional[Phase] = None,
        group: Optional[str] = None,
        lease: AttemptLease | None = None,
    ) -> None:
        """按 kind 路由健康写入;旧式(无 kind)→ member 级计数(旧行为)。"""
        self._health.end_attempt(lease)
        config = self.config
        member_key = _member_key(scope, member)
        if kind is FailureKind.RATE_LIMITED:
            self._health.throttle_member(member_key)
            return
        if kind is not None and kind in (FailureKind.INVALID_REQUEST, FailureKind.CAPABILITY):
            return
        # least_failures 排序参考:真实失败(含旧式)都计;RATE_LIMITED 上面已节流返回
        with self._lock:
            self._failures[(scope, member)] = self._failures.get((scope, member), 0) + 1
        if kind is None or kind is FailureKind.TASK_FAILED:
            self._health.record_failure(
                member_key,
                kind=kind or FailureKind.PROVIDER_FAULT,
                phase=phase,
                threshold=config.failure_threshold,
                base_cooldown=config.cooldown_base(),
                max_cooldown=config.max_cooldown,
            )
            return
        # PROVIDER_FAULT / AUTH_OR_QUOTA → group 级
        self._health.record_failure(
            _group_key(group or member),
            kind=kind,
            phase=phase,
            threshold=config.failure_threshold,
            base_cooldown=config.cooldown_base(),
            max_cooldown=config.max_cooldown,
        )

    def health_snapshot(self) -> dict[str, dict[str, Union[float, int, bool, str]]]:
        """健康快照(供 /models/summary 与调试命令;旧键保留,新字段只增)"""
        return self._health.snapshot()


_default_balancer: Optional[LoadBalancer] = None


def _resolve_plugin_config() -> BalancerConfig:
    """每次决策时从 PLUGIN_CONFIG 读最新策略/阈值/冷却(网页控制台改完即生效)"""
    mode = "round_robin"
    threshold = 3
    base = 30.0
    max_cool = 1800.0
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

    def _float_key(key: str) -> Optional[float]:
        try:
            raw = PLUGIN_CONFIG.get_config(key).data
        except KeyError:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    v = _float_key("Circuit_Base_Cooldown")
    if v is not None and v > 0:
        base = v
    v = _float_key("Circuit_Max_Cooldown")
    if v is not None and v > 0:
        max_cool = v
    return BalancerConfig(
        mode=mode,
        failure_threshold=threshold,
        base_cooldown=base,
        max_cooldown=max_cool,
    )


def get_default_balancer() -> LoadBalancer:
    """全局单例;策略/阈值经 config_resolver 每次实时读 PLUGIN_CONFIG
    (配置键不存在时——如测试环境——resolver 抛 KeyError 回落内置默认)"""
    global _default_balancer
    if _default_balancer is None:
        _default_balancer = LoadBalancer(config_resolver=_resolve_plugin_config)
    return _default_balancer


__all__ = [
    "AttemptLease",
    "LoadBalancer",
    "BalancerConfig",
    "get_default_balancer",
]
