"""RH_ComfyUI Web 控制台 — 模型与供应商全景快照。

只读聚合四个数据源(全部是配置/内存态,零网络):

- ``core.routing.registry.model_registry`` — 模型实例与其通道绑定。
  **绑定顺序就是负载均衡的输入顺序**,页面上的「供应商顺序」即此处的下标。
- ``core.routing.balancer`` — 连续失败次数与熔断状态,按 (模型, 通道) 二级 key。
- ``core.dispatch.concurrency`` — 供应商在途请求数与并发上限(RH 三通道共享一把闸)。
- ``rh_config.PLUGIN_CONFIG`` — 负载均衡策略与熔断阈值(控制台改完即生效)。

供应商可用性是 **(模型, 通道) 粒度** 的:同一家的启用闸按模型名逐个判定
(如 ComfyUI 的工作流白名单),所以「供应商可用」只在继承模型这一层聚合后才有意义。
"""

from __future__ import annotations

from typing import TypedDict
from datetime import datetime

from gsuid_core.logger import logger

from ..rh_models.api import channel_supports_cancel
from ..core.base.generation import AIGCGenerationBase
from ..core.channels.channel import ChannelBinding, ProviderChannel


class ChannelView(TypedDict):
    """模型下的一条供应商通道"""

    name: str
    position: int
    vendor_model: str
    weight: int
    available: bool
    supports_cancel: bool
    supports_remote_cancel: bool
    failure_count: int
    circuit_open: bool
    inflight: int
    concurrency_limit: int


class ModelView(TypedDict):
    """一个模型及其供应商通道"""

    name: str
    display_name: str
    catalog_group: str
    group_label: str
    backend: str
    available: bool
    unavailable_reason: str
    priority: int
    point_cost: int
    point_min: int
    point_max: int
    execution_mode: str
    supports_cancel: bool
    supports_remote_cancel: bool
    channels: list[ChannelView]


class ProviderView(TypedDict):
    """一家供应商(通道实例)及其继承的模型"""

    name: str
    label: str
    weight: int
    model_count: int
    available_model_count: int
    unavailable_model_count: int
    fully_available: bool
    supports_remote_cancel: bool
    inflight: int
    concurrency_limit: int
    failure_total: int
    circuit_open_models: list[str]
    models: list[str]


class ModalityStat(TypedDict):
    key: str
    label: str
    total: int
    available: int


class BalancerView(TypedDict):
    mode: str
    failure_threshold: int
    circuit_breaker_seconds: float


class Totals(TypedDict):
    models: int
    models_available: int
    providers: int
    providers_healthy: int
    links: int
    links_available: int
    circuit_open: int


class DashboardSnapshot(TypedDict):
    generated_at: str
    balancer: BalancerView
    totals: Totals
    modalities: list[ModalityStat]
    providers: list[ProviderView]
    models: list[ModelView]


# 通道名 → 人类可读标签。取不到时直接用通道名本身,不猜。
_CHANNEL_LABELS: dict[str, str] = {
    "ark": "火山方舟 ARK",
    "azure": "Azure GPT-Image",
    "comfyui": "ComfyUI 工作流",
    "dashscope": "阿里云百炼 DashScope",
    "dashscope_image": "DashScope 生图",
    "dashscope_tts": "DashScope 语音合成",
    "fishaudio": "Fish Audio",
    "gemini": "Gemini 生图",
    "gemini_speech": "Gemini 语音",
    "gpt-image-2": "GPT Image 2",
    "gpt_image2": "GPT Image 2",
    "happyhorse": "HappyHorse 视频",
    "kling": "快手可灵",
    "mimo": "小米 MiMo TTS",
    "minimax": "MiniMax",
    "minimax-h3": "MiniMax H3 视频",
    "qwen": "Qwen 图像",
    "rh_app": "RunningHub AI 应用",
    "runninghub": "RunningHub Seedance",
    "seedance": "Seedance 视频",
    "seedream": "Seedream 图片",
    "tx_aiart": "腾讯云混元",
    "wan30": "Wan 3.0 视频",
}


def channel_label(name: str) -> str:
    """通道名 → 显示标签;未登记的通道(外部插件注入)回落到原名。"""
    return _CHANNEL_LABELS.get(name, name)


async def _safe_bindings(model: AIGCGenerationBase) -> list[ChannelBinding]:
    """取通道绑定。外部插件注册的模型不受控,抛错时按「无通道」处理。"""
    try:
        return model.channel_bindings()
    except Exception as e:  # noqa: BLE001 — 外部插件模型不可信,不能拖垮控制台
        logger.warning(f"[rh_webconsole] 模型 {model.name} 的 channel_bindings() 抛错,按空通道处理: {e}")
        return []


async def _safe_available(model: AIGCGenerationBase) -> bool:
    """模型可用性。外部插件模型抛错时按不可用处理。"""
    try:
        return await model.check_available()
    except Exception as e:  # noqa: BLE001 — 外部插件模型不可信
        logger.warning(f"[rh_webconsole] 模型 {model.name} 的 check_available() 抛错: {e}")
        return False


async def _safe_reason(model: AIGCGenerationBase) -> str:
    """不可用原因;取不到时给一句兜底文案。"""
    try:
        return await model.unavailable_reason()
    except Exception as e:  # noqa: BLE001 — 外部插件模型不可信
        return f"未知原因({e})"


async def _safe_channel_available(channel: ProviderChannel) -> bool:
    """通道可用性(零网络,只读配置)。"""
    try:
        return await channel.check_available()
    except Exception as e:  # noqa: BLE001 — 外部插件通道不可信
        logger.warning(f"[rh_webconsole] 通道 {channel.name} 的 check_available() 抛错: {e}")
        return False


def _catalog_group(model: AIGCGenerationBase) -> str:
    """目录分组:NodeDef.catalog_group 优先,否则取模态。"""
    node = model.node
    if node is None:
        return model.modality.value
    if node.catalog_group:
        return node.catalog_group
    return node.task_type.value


def _point_range(model: AIGCGenerationBase) -> tuple[int, int]:
    """积分区间;外部模型覆盖 point_range() 失败时回落静态价。"""
    try:
        low, high = model.point_range()
    except Exception as e:  # noqa: BLE001 — 外部插件模型不可信
        logger.warning(f"[rh_webconsole] 模型 {model.name} 的 point_range() 抛错: {e}")
        return (model.point_cost, model.point_cost)
    return (low, high)


async def build_dashboard_snapshot() -> DashboardSnapshot:
    """聚合一次全景快照(只读)。"""
    from ..utils.core.request import CATALOG_GROUP_DISPLAY
    from ..core.routing.balancer import get_default_balancer
    from ..core.routing.registry import model_registry
    from ..core.dispatch.concurrency import channel_load_snapshot

    balancer = get_default_balancer()
    health = balancer.health_snapshot()
    registered = model_registry.all_models()

    # ── 第一遍:收齐通道名与绑定(顺序 = 负载均衡输入顺序) ──
    bindings_by_model: list[tuple[AIGCGenerationBase, list[ChannelBinding]]] = []
    channel_names: list[str] = []
    for model in registered:
        bindings = await _safe_bindings(model)
        bindings_by_model.append((model, bindings))
        for binding in bindings:
            if binding.channel.name not in channel_names:
                channel_names.append(binding.channel.name)

    load = channel_load_snapshot(channel_names)

    # ── 第二遍:逐模型逐通道展开 ──
    model_views: list[ModelView] = []
    provider_models: dict[str, list[str]] = {}
    provider_available_count: dict[str, int] = {}
    provider_circuit_models: dict[str, list[str]] = {}
    provider_failure_total: dict[str, int] = {}
    provider_weight: dict[str, int] = {}
    provider_remote_cancel: dict[str, bool] = {}

    for model, bindings in bindings_by_model:
        available = await _safe_available(model)
        reason = "" if available else await _safe_reason(model)
        low, high = _point_range(model)
        group = _catalog_group(model)
        node = model.node

        channels: list[ChannelView] = []
        for position, binding in enumerate(bindings):
            channel = binding.channel
            name = channel.name
            entry = health.get(f"{model.name}/{name}")
            failure_count = int(entry["failure_count"]) if entry is not None else 0
            circuit_open = bool(entry["circuit_open"]) if entry is not None else False
            pool = load.get(name)
            inflight = pool["inflight"] if pool is not None else 0
            limit = pool["limit"] if pool is not None else 0
            remote_cancel = bool(channel.supports_remote_cancel())
            channel_available = await _safe_channel_available(channel)

            channels.append(
                ChannelView(
                    name=name,
                    position=position,
                    vendor_model=binding.vendor_model or "",
                    weight=channel.weight,
                    available=channel_available,
                    supports_cancel=channel_supports_cancel(model, name),
                    supports_remote_cancel=remote_cancel,
                    failure_count=failure_count,
                    circuit_open=circuit_open,
                    inflight=inflight,
                    concurrency_limit=limit,
                )
            )

            provider_models.setdefault(name, []).append(model.name)
            provider_available_count[name] = provider_available_count.get(name, 0) + (1 if channel_available else 0)
            provider_failure_total[name] = provider_failure_total.get(name, 0) + failure_count
            provider_weight[name] = max(provider_weight.get(name, 1), channel.weight)
            provider_remote_cancel[name] = provider_remote_cancel.get(name, False) or remote_cancel
            if circuit_open:
                provider_circuit_models.setdefault(name, []).append(model.name)

        model_views.append(
            ModelView(
                name=model.name,
                display_name=model.display_name,
                catalog_group=group,
                group_label=CATALOG_GROUP_DISPLAY.get(group, group),
                backend=node.backend if node is not None else "",
                available=available,
                unavailable_reason=reason,
                priority=model.priority,
                point_cost=model.point_cost,
                point_min=low,
                point_max=high,
                execution_mode=model.execution_mode,
                supports_cancel=bool(model.supports_cancel),
                supports_remote_cancel=any(c["supports_remote_cancel"] for c in channels),
                channels=channels,
            )
        )

    # ── 供应商聚合(按首次出现顺序 = 声明顺序) ──
    provider_views: list[ProviderView] = []
    for name in channel_names:
        models_of = provider_models.get(name, [])
        available_count = provider_available_count.get(name, 0)
        pool = load.get(name)
        provider_views.append(
            ProviderView(
                name=name,
                label=channel_label(name),
                weight=provider_weight.get(name, 1),
                model_count=len(models_of),
                available_model_count=available_count,
                unavailable_model_count=len(models_of) - available_count,
                fully_available=bool(models_of) and available_count == len(models_of),
                supports_remote_cancel=provider_remote_cancel.get(name, False),
                inflight=pool["inflight"] if pool is not None else 0,
                concurrency_limit=pool["limit"] if pool is not None else 0,
                failure_total=provider_failure_total.get(name, 0),
                circuit_open_models=provider_circuit_models.get(name, []),
                models=models_of,
            )
        )

    # ── 模态统计 ──
    modality_order: list[str] = []
    modality_total: dict[str, int] = {}
    modality_available: dict[str, int] = {}
    for view in model_views:
        key = view["catalog_group"]
        if key not in modality_total:
            modality_order.append(key)
            modality_total[key] = 0
            modality_available[key] = 0
        modality_total[key] += 1
        if view["available"]:
            modality_available[key] += 1

    modalities: list[ModalityStat] = [
        ModalityStat(
            key=key,
            label=CATALOG_GROUP_DISPLAY.get(key, key),
            total=modality_total[key],
            available=modality_available[key],
        )
        for key in modality_order
    ]

    links = sum(len(v["channels"]) for v in model_views)
    links_available = sum(1 for v in model_views for c in v["channels"] if c["available"])
    circuit_open = sum(1 for v in model_views for c in v["channels"] if c["circuit_open"])

    totals = Totals(
        models=len(model_views),
        models_available=sum(1 for v in model_views if v["available"]),
        providers=len(provider_views),
        providers_healthy=sum(1 for p in provider_views if p["fully_available"]),
        links=links,
        links_available=links_available,
        circuit_open=circuit_open,
    )

    config = balancer.config
    balancer_view = BalancerView(
        mode=config.mode,
        failure_threshold=config.failure_threshold,
        circuit_breaker_seconds=config.circuit_breaker_seconds,
    )

    return DashboardSnapshot(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        balancer=balancer_view,
        totals=totals,
        modalities=modalities,
        providers=provider_views,
        models=model_views,
    )


__all__ = [
    "ChannelView",
    "ModelView",
    "ProviderView",
    "ModalityStat",
    "BalancerView",
    "Totals",
    "DashboardSnapshot",
    "build_dashboard_snapshot",
    "channel_label",
]
