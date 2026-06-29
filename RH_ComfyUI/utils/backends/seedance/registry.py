"""Seedance Provider 注册表与选择器

每个供应商通过 Seedance_Enable_{name} 开关独立启用/禁用,
启用的供应商按负载均衡策略排序后参与任务分发。

配置:
  - Seedance_Enable_ark / Seedance_Enable_gateway / Seedance_Enable_runninghub: bool
    各供应商启用开关
  - Seedance_Load_Balance: "round_robin" | "weighted" | "least_failures"
    负载均衡策略
  - Seedance_Failure_Threshold: 连续失败次数阈值,超过后熔断该供应商(默认 3)
"""

from __future__ import annotations

import time
import random
import threading
from typing import Any, Optional

from gsuid_core.logger import logger

from .provider import SeedanceProvider
from .providers import (
    ArkSeedanceProvider,
    GatewaySeedanceProvider,
    RunningHubSeedanceProvider,
)

_PROVIDERS: dict[str, type[SeedanceProvider]] = {
    "ark": ArkSeedanceProvider,
    "gateway": GatewaySeedanceProvider,
    "runninghub": RunningHubSeedanceProvider,
}

DEFAULT_PROVIDER = "ark"

# ── 负载均衡状态(进程级) ──
_lb_lock = threading.Lock()
_health_lock = threading.Lock()  # 保护 _failure_counts / _circuit_breaker_until
_rr_index: int = 0  # round_robin 全局索引
_failure_counts: dict[str, int] = {}  # provider_name → 连续失败次数
_circuit_breaker_until: dict[str, float] = {}  # provider_name → 熔断恢复时间戳


def get_provider(
    name: str,
    *,
    api_key: str = "",
    base_url: Optional[str] = None,
    dry_run: bool = False,
) -> SeedanceProvider:
    """根据名称实例化 Provider(名称非法时回退到 ARK)。"""
    cls = _PROVIDERS.get(name, ArkSeedanceProvider)
    return cls(api_key=api_key, base_url=base_url, dry_run=dry_run)


def resolve_provider_name(
    *,
    node_provider: Optional[str],
    configured: Optional[str],
    base_url: Optional[str],
) -> str:
    """优先级:节点级 > 全局选择器 > base_url 自动识别 > 默认 ark。(兼容旧逻辑)"""
    if node_provider:
        return node_provider
    if configured and configured != "auto":
        return configured
    if base_url:
        low = base_url.lower()
        if "volces.com" in low:
            return "ark"
        if "runninghub.cn" in low:
            return "runninghub"
        return "gateway"
    return DEFAULT_PROVIDER


def resolve_provider_candidates(
    *,
    node_provider: Optional[str],
    configured: Optional[str],
    base_url: Optional[str],
    load_balance_mode: str = "round_robin",
    failure_threshold: int = 3,
    circuit_breaker_duration: float = 120.0,
    _enabled_names: Optional[list[str]] = None,
) -> list[str]:
    """返回有序候选 provider 名列表,用于负载均衡 + 故障切换。

    Args:
        node_provider: YAML 节点级固定供应商(有值时直接返回单元素列表)
        configured: 已弃用,保留兼容
        base_url: 已弃用,保留兼容
        load_balance_mode: "round_robin" / "weighted" / "least_failures"
        failure_threshold: 连续失败多少次后熔断
        circuit_breaker_duration: 熔断持续时间(秒)
        _enabled_names: 已启用的供应商名列表(由上层按 Seedance_Enable_* 过滤)

    Returns:
        候选 provider 名列表,排在前面的是优先使用的。
        列表为空表示没有可用供应商。
    """
    # 节点级固定供应商 → 单元素列表,不做负载均衡
    if node_provider:
        return [node_provider]

    # 确定可用候选:优先使用上层传入的启用列表
    if _enabled_names is not None:
        candidate_pool = _enabled_names
    elif configured and configured not in ("auto", "all"):
        # 兼容旧配置:全局指定了具体供应商
        return [configured]
    else:
        candidate_pool = list(_PROVIDERS.keys())

    # 按 base_url 推断偏好(仅当 _enabled_names 未指定时)
    preferred: Optional[str] = None
    if base_url and _enabled_names is None:
        low = base_url.lower()
        if "volces.com" in low:
            preferred = "ark"
        elif "runninghub.cn" in low:
            preferred = "runninghub"
        else:
            preferred = "gateway"

    # 过滤掉熔断中的供应商
    now = time.time()
    available: list[str] = []
    with _health_lock:
        for name in candidate_pool:
            expire_at = _circuit_breaker_until.get(name, 0)
            if now < expire_at:
                continue  # 仍在熔断期,跳过
            available.append(name)

    if not available:
        # 全部熔断:逐个检查各供应商的熔断剩余时间,让剩余最短的那个先"自然解封",
        # 而不是清空全局熔断窗口。这样单个持续失败的供应商不会周期性解除
        # 全部供应商的熔断,避免放大故障面。
        with _health_lock:
            earliest = min(
                _circuit_breaker_until.items(),
                key=lambda kv: kv[1],
                default=(None, float("inf")),
            )
            if earliest[0] is not None:
                remaining = earliest[1] - now
                logger.warning(
                    f"[Seedance] 全部供应商已熔断,最短剩余 {remaining:.0f}s "
                    f"({earliest[0]}),等待自然解封"
                )
            # 极端兜底:如果全部熔断时 `_circuit_breaker_until` 已被外部清空
            # (理论上不会发生),回退到完整候选池。
            available = list(candidate_pool)

    # 如果 base_url 指定了偏好供应商,将其排在首位
    if preferred and preferred in available:
        available.remove(preferred)
        available.insert(0, preferred)

    # 按负载均衡策略排序
    if len(available) <= 1:
        return available

    if load_balance_mode == "round_robin":
        with _lb_lock:
            global _rr_index
            idx = _rr_index % len(available)
            _rr_index += 1
        # 轮转:从 idx 位置开始
        return available[idx:] + available[:idx]

    elif load_balance_mode == "weighted":
        # 权重:ark=3, gateway=2, runninghub=1(官方优先)
        weights = {"ark": 3, "gateway": 2, "runninghub": 1}
        weighted: list[str] = []
        for name in available:
            w = max(weights.get(name, 1), 1)
            weighted.extend([name] * w)
        chosen = random.choice(weighted)
        # 排首位,其余保持原序
        result = [chosen]
        for n in available:
            if n != chosen:
                result.append(n)
        return result

    elif load_balance_mode == "least_failures":
        # 按连续失败次数升序排列(失败最少的排前面)
        with _health_lock:
            return sorted(available, key=lambda n: _failure_counts.get(n, 0))

    else:
        return available


def record_provider_success(provider_name: str) -> None:
    """记录供应商成功,重置连续失败计数。"""
    with _health_lock:
        _failure_counts.pop(provider_name, None)
        _circuit_breaker_until.pop(provider_name, None)


def record_provider_failure(
    provider_name: str,
    *,
    failure_threshold: int = 3,
    circuit_breaker_duration: float = 120.0,
) -> None:
    """记录供应商失败,超过阈值后熔断。"""
    with _health_lock:
        count = _failure_counts.get(provider_name, 0) + 1
        _failure_counts[provider_name] = count
        if count >= failure_threshold:
            _circuit_breaker_until[provider_name] = time.time() + circuit_breaker_duration


def get_provider_health() -> dict[str, dict[str, Any]]:
    """返回各供应商健康状态(供监控/调试)。"""
    now = time.time()
    result: dict[str, dict[str, Any]] = {}
    with _health_lock:
        for name in _PROVIDERS:
            expire_at = _circuit_breaker_until.get(name, 0)
            result[name] = {
                "failure_count": _failure_counts.get(name, 0),
                "circuit_breaker_active": now < expire_at,
                "circuit_breaker_remaining": max(0, expire_at - now),
            }
    return result


def known_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def get_all_providers() -> dict[str, type[SeedanceProvider]]:
    """返回全部已注册的供应商类型映射表(只读)。

    替代直接 import _PROVIDERS 的私有成员访问方式。
    """
    return dict(_PROVIDERS)


__all__ = [
    "get_provider",
    "resolve_provider_name",
    "resolve_provider_candidates",
    "record_provider_success",
    "record_provider_failure",
    "get_provider_health",
    "known_providers",
    "get_all_providers",
    "DEFAULT_PROVIDER",
]
