"""Seedance Provider 注册表与负载均衡(开源/外部插件统一扩展点)

供应商通过 register_provider() 注册进本表:
- 开源内置:ark / runninghub(本模块 import 时注册)
- 外部插件:如 aigc_system 在自己的 @on_core_start 里注册 gateway 供应商,
  与内置供应商共享同一套负载均衡、熔断计数与计费/统计链路。

每个注册项携带:
- cls:            SeedanceProvider 子类
- weight:         weighted 负载均衡权重
- config_resolver: 凭证解析回调,返回 ProviderCredentials(enabled/api_key/base_url);
                   凭证可以来自任意插件自己的配置面板
- accepts_model_field: 创建请求体是否接受 model 字段(RunningHub 端点即模型,为 False)

跨插件模型映射:register_vendor_models() 允许外部插件为既有 Pipeline
(如 seedance2)补充"该供应商下的厂商模型 ID",使其参与该 Pipeline 的负载均衡。

负载均衡配置(SERVICE_CONFIG):
  - Seedance_Load_Balance: "round_robin" | "weighted" | "least_failures"
  - Seedance_Failure_Threshold: 连续失败次数阈值,超过后熔断该供应商(默认 3)
"""

from __future__ import annotations

import time
import random
import threading
from typing import Any, Callable, Optional
from dataclasses import dataclass

from gsuid_core.logger import logger

from .provider import SeedanceProvider
from .providers import ArkSeedanceProvider, RunningHubSeedanceProvider

DEFAULT_PROVIDER = "ark"


@dataclass(frozen=True)
class ProviderCredentials:
    """一次凭证解析的结果(由 config_resolver 返回)"""

    enabled: bool = False
    api_key: str = ""
    base_url: Optional[str] = None


ConfigResolver = Callable[[], ProviderCredentials]


@dataclass(frozen=True)
class ProviderRegistration:
    """一条供应商注册项"""

    cls: type[SeedanceProvider]
    weight: int = 1
    config_resolver: Optional[ConfigResolver] = None
    accepts_model_field: bool = True

    def credentials(self) -> ProviderCredentials:
        if self.config_resolver is not None:
            return self.config_resolver()
        return _service_config_credentials(self.cls.name, self.cls.DEFAULT_BASE_URL)


def _service_config_credentials(name: str, default_base_url: str) -> ProviderCredentials:
    """内置供应商的默认凭证方案:读 RH_ComfyUI 自己的 SERVICE_CONFIG

    键名约定: Seedance_Enable_{name} / Seedance_apikey_{name} / Seedance_BaseURL_{name};
    runninghub 的 key 为空时回退 RH_apikey。
    """
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    def _get(key: str) -> Any:
        try:
            return SERVICE_CONFIG.get_config(key).data
        except Exception:
            return None

    enabled = bool(_get(f"Seedance_Enable_{name}"))
    api_key = str(_get(f"Seedance_apikey_{name}") or "")
    if name == "runninghub" and not api_key:
        api_key = str(_get("RH_apikey") or "")
    base_url = str(_get(f"Seedance_BaseURL_{name}") or "").strip() or None
    return ProviderCredentials(
        enabled=enabled, api_key=api_key, base_url=base_url or (default_base_url or None)
    )


# ── 注册表状态 ──

_registry_lock = threading.Lock()
_REGISTRY: dict[str, ProviderRegistration] = {}
# provider_name → {pipeline_name → vendor model id}(外部插件为既有模型补充映射)
_VENDOR_MODELS: dict[str, dict[str, str]] = {}


def register_provider(
    cls: type[SeedanceProvider],
    *,
    weight: int = 1,
    config_resolver: Optional[ConfigResolver] = None,
    accepts_model_field: bool = True,
) -> None:
    """注册一个 Seedance 供应商(同名后注册者覆盖并告警)"""
    name = cls.name
    if not name:
        raise ValueError(f"供应商类 {cls.__qualname__} 缺少 name 属性")
    with _registry_lock:
        if name in _REGISTRY:
            logger.warning(f"[Seedance] 供应商 {name} 被重复注册,新实现覆盖旧实现")
        _REGISTRY[name] = ProviderRegistration(
            cls=cls,
            weight=weight,
            config_resolver=config_resolver,
            accepts_model_field=accepts_model_field,
        )
    logger.info(f"[Seedance] 注册供应商: {name} (weight={weight})")


def get_registration(name: str) -> Optional[ProviderRegistration]:
    return _REGISTRY.get(name)


def registered_providers() -> dict[str, ProviderRegistration]:
    """全部已注册供应商(只读快照)"""
    return dict(_REGISTRY)


def known_providers() -> list[str]:
    return list(_REGISTRY.keys())


def register_vendor_models(provider_name: str, mapping: dict[str, str]) -> None:
    """为既有 Pipeline 补充该供应商下的厂商模型 ID(外部插件扩展点)

    例: register_vendor_models("gateway", {"seedance2": "dreamina-seedance-2.0"})
    使 seedance2 的负载均衡候选中加入 gateway 通道。
    """
    with _registry_lock:
        _VENDOR_MODELS.setdefault(provider_name, {}).update(mapping)
    logger.info(f"[Seedance] 供应商 {provider_name} 补充 {len(mapping)} 条模型映射")


def vendor_model_for(provider_name: str, pipeline_name: str) -> Optional[str]:
    return _VENDOR_MODELS.get(provider_name, {}).get(pipeline_name) or None


def get_provider(
    name: str,
    *,
    api_key: str = "",
    base_url: Optional[str] = None,
    dry_run: bool = False,
) -> SeedanceProvider:
    """根据名称实例化 Provider。未注册的名称抛 KeyError。"""
    reg = _REGISTRY.get(name)
    if reg is None:
        raise KeyError(f"Seedance 供应商 {name!r} 未注册(已注册: {known_providers()})")
    return reg.cls(api_key=api_key, base_url=base_url, dry_run=dry_run)


# ── 负载均衡状态(进程级;跨插件供应商共享同一份) ──
_lb_lock = threading.Lock()
_health_lock = threading.Lock()  # 保护 _failure_counts / _circuit_breaker_until
_rr_index: int = 0  # round_robin 全局索引
_failure_counts: dict[str, int] = {}  # provider_name → 连续失败次数
_circuit_breaker_until: dict[str, float] = {}  # provider_name → 熔断恢复时间戳


def order_candidates(
    enabled_names: list[str],
    *,
    load_balance_mode: str = "round_robin",
) -> list[str]:
    """把已启用的供应商按负载均衡策略排序,熔断中的暂时剔除。

    Returns:
        候选 provider 名列表,排在前面的优先使用;空表示没有可用供应商。
    """
    # 过滤掉熔断中的供应商
    now = time.time()
    available: list[str] = []
    with _health_lock:
        for name in enabled_names:
            if now < _circuit_breaker_until.get(name, 0):
                continue  # 仍在熔断期,跳过
            available.append(name)

    if not available:
        # 全部熔断:提示最短剩余时间,并回退到完整候选池兜底
        # (让剩余最短的先"自然解封",而不是清空全局熔断窗口)
        with _health_lock:
            earliest = min(
                _circuit_breaker_until.items(),
                key=lambda kv: kv[1],
                default=(None, float("inf")),
            )
        if earliest[0] is not None:
            logger.warning(
                f"[Seedance] 全部供应商已熔断,最短剩余 {earliest[1] - now:.0f}s "
                f"({earliest[0]}),兜底重试完整候选池"
            )
        available = list(enabled_names)

    if len(available) <= 1:
        return available

    if load_balance_mode == "round_robin":
        with _lb_lock:
            global _rr_index
            idx = _rr_index % len(available)
            _rr_index += 1
        return available[idx:] + available[:idx]

    if load_balance_mode == "weighted":
        weighted: list[str] = []
        for name in available:
            reg = _REGISTRY.get(name)
            w = max(reg.weight if reg else 1, 1)
            weighted.extend([name] * w)
        chosen = random.choice(weighted)
        return [chosen] + [n for n in available if n != chosen]

    if load_balance_mode == "least_failures":
        with _health_lock:
            return sorted(available, key=lambda n: _failure_counts.get(n, 0))

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
    """记录供应商失败,超过阈值后熔断。failure_threshold<=0 表示不熔断。"""
    with _health_lock:
        count = _failure_counts.get(provider_name, 0) + 1
        _failure_counts[provider_name] = count
        if failure_threshold > 0 and count >= failure_threshold:
            _circuit_breaker_until[provider_name] = time.time() + circuit_breaker_duration


def get_failure_count(provider_name: str) -> int:
    with _health_lock:
        return _failure_counts.get(provider_name, 0)


def get_provider_health() -> dict[str, dict[str, Any]]:
    """返回各供应商健康状态(供监控/调试)。"""
    now = time.time()
    result: dict[str, dict[str, Any]] = {}
    with _health_lock:
        for name in _REGISTRY:
            expire_at = _circuit_breaker_until.get(name, 0)
            result[name] = {
                "failure_count": _failure_counts.get(name, 0),
                "circuit_breaker_active": now < expire_at,
                "circuit_breaker_remaining": max(0, expire_at - now),
            }
    return result


# ── 内置供应商注册(import 即注册) ──
register_provider(ArkSeedanceProvider, weight=3)
register_provider(RunningHubSeedanceProvider, weight=1, accepts_model_field=False)


__all__ = [
    "ProviderCredentials",
    "ProviderRegistration",
    "register_provider",
    "register_vendor_models",
    "vendor_model_for",
    "get_registration",
    "registered_providers",
    "get_provider",
    "order_candidates",
    "record_provider_success",
    "record_provider_failure",
    "get_failure_count",
    "get_provider_health",
    "known_providers",
    "DEFAULT_PROVIDER",
]
