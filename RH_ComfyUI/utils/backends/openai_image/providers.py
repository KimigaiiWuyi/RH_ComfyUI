"""OpenAI 兼容供应商池 — 读配置(GsRepeatGroupConfig)并把每家挂到现有模型上。

配置键按「协议_模态_Providers」命名(当前仅图片池 ``OpenAI_Image_Providers``):
每行一家供应商(enable/name/base_url/api_key/weight + 一组 model_real_name→model_id
映射)。``sync_openai_image_providers()`` 在启动/改配置后调用:按当前配置
register_binding 到 channel_registry, 与内置通道一起参与负载均衡。凭证本身
每请求实时读(热更新), 只有供应商增删/映射/权重变动才需重新 sync。

扩展到其他模态时:新增配置键(如 ``OpenAI_Speech_Providers``,行结构不变),
在 ``_POOLS`` 追加一条 (配置键, 通道工厂) 即可复用全部解析/挂载逻辑。
"""

from __future__ import annotations

from typing import List, Callable
from dataclasses import field, dataclass

from gsuid_core.logger import logger
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsBoolConfig, GsRepeatGroupConfig

from .channel import OpenAIImageChannel, OpenAIImageCredentials
from ....core.channels.channel import ProviderChannel
from ....core.channels.registry import channel_registry
from ....rh_config.comfyui_config import SERVICE_CONFIG

_PROVIDERS_KEY = "OpenAI_Image_Providers"

# 本层已注入的 (model_real_name, channel_name), 供 resync 时先清除再重挂。
_REGISTERED: List[tuple[str, str]] = []


@dataclass
class ProviderModelBinding:
    model_real_name: str
    model_id: str


@dataclass
class ProviderEntry:
    name: str
    enable: bool
    base_url: str
    api_key: str
    weight: int = 1
    models: List[ProviderModelBinding] = field(default_factory=list)


def _str_field(row: dict, key: str) -> str:
    if key in row and isinstance(row[key], GsStrConfig):
        return str(row[key].data or "")
    return ""


def _bool_field(row: dict, key: str) -> bool:
    if key in row and isinstance(row[key], GsBoolConfig):
        return bool(row[key].data)
    return False


def _int_field(row: dict, key: str, default: int) -> int:
    """数值行字段(网页端以 GsStrConfig 承载);非法/缺失回落 default。"""
    raw = _str_field(row, key)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def resolve_provider_entries(config_key: str = _PROVIDERS_KEY) -> List[ProviderEntry]:
    """从 SERVICE_CONFIG 实时解析供应商列表(每次调用重读, 反映最新配置)。"""
    cfg = SERVICE_CONFIG.get_config(config_key)
    if not isinstance(cfg, GsRepeatGroupConfig):
        return []
    entries: List[ProviderEntry] = []
    for row in cfg.data:
        name = _str_field(row, "name")
        if not name:
            continue
        models: List[ProviderModelBinding] = []
        models_field = row["models"] if "models" in row else None
        if isinstance(models_field, GsRepeatGroupConfig):
            for m in models_field.data:
                real = _str_field(m, "model_real_name")
                vid = _str_field(m, "model_id")
                if real and vid:
                    models.append(ProviderModelBinding(real, vid))
        entries.append(
            ProviderEntry(
                name=name,
                enable=_bool_field(row, "enable"),
                base_url=_str_field(row, "base_url"),
                api_key=_str_field(row, "api_key"),
                weight=_int_field(row, "weight", 1),
                models=models,
            )
        )
    return entries


def _make_resolver(config_key: str, provider_name: str):
    def _resolve() -> OpenAIImageCredentials:
        for entry in resolve_provider_entries(config_key):
            if entry.name == provider_name:
                return OpenAIImageCredentials(entry.enable, entry.api_key, entry.base_url)
        return OpenAIImageCredentials(False, "", "")

    return _resolve


def _make_image_channel(config_key: str, entry: ProviderEntry) -> ProviderChannel:
    return OpenAIImageChannel(
        entry.name,
        credentials_resolver=_make_resolver(config_key, entry.name),
        weight=entry.weight,
    )


# 池规格:(配置键, 通道工厂)。新模态池在此追加一条即可复用同一套挂载逻辑。
_POOLS: List[tuple[str, Callable[[str, ProviderEntry], ProviderChannel]]] = [
    (_PROVIDERS_KEY, _make_image_channel),
]


def sync_openai_image_providers() -> None:
    """按当前配置重挂全部供应商池: 先清除本层历史注入, 再按启用项 register_binding。"""
    for model_name, channel_name in _REGISTERED:
        channel_registry.unregister(model_name, channel_name)
    _REGISTERED.clear()

    total = 0
    enabled_total = 0
    for config_key, make_channel in _POOLS:
        seen_names: set[str] = set()
        for entry in resolve_provider_entries(config_key):
            if not entry.enable:
                continue
            if entry.name in seen_names:
                logger.warning(f"[OpenAIImage] 供应商名重复, 已跳过后者: {entry.name}")
                continue
            seen_names.add(entry.name)
            channel = make_channel(config_key, entry)
            for b in entry.models:
                channel_registry.register_binding(b.model_real_name, channel, vendor_model=b.model_id)
                _REGISTERED.append((b.model_real_name, entry.name))
                total += 1
        enabled_total += len(seen_names)
    logger.info(f"[OpenAIImage] 供应商池已同步: {enabled_total} 家启用, {total} 条模型绑定")


__all__ = ["sync_openai_image_providers", "resolve_provider_entries", "ProviderEntry"]
