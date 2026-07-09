"""OpenAI 兼容供应商池 — 读配置(GsRepeatGroupConfig)并把每家挂到现有模型上。

配置键 ``OpenAI_Image_Providers``: 每行一家供应商(enable/name/base_url/api_key + 一组
model_real_name→model_id 映射)。``sync_openai_image_providers()`` 在启动/改配置后调用:
按当前配置 register_binding 到 channel_registry, 与内置通道一起参与负载均衡。凭证本身
每请求实时读(热更新), 只有供应商增删/映射变动才需重新 sync。
"""

from __future__ import annotations

from typing import List
from dataclasses import field, dataclass

from gsuid_core.logger import logger
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsBoolConfig, GsRepeatGroupConfig

from .channel import OpenAIImageChannel, OpenAIImageCredentials
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
    models: List[ProviderModelBinding] = field(default_factory=list)


def _str_field(row: dict, key: str) -> str:
    if key in row and isinstance(row[key], GsStrConfig):
        return str(row[key].data or "")
    return ""


def _bool_field(row: dict, key: str) -> bool:
    if key in row and isinstance(row[key], GsBoolConfig):
        return bool(row[key].data)
    return False


def resolve_provider_entries() -> List[ProviderEntry]:
    """从 SERVICE_CONFIG 实时解析供应商列表(每次调用重读, 反映最新配置)。"""
    cfg = SERVICE_CONFIG.get_config(_PROVIDERS_KEY)
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
                models=models,
            )
        )
    return entries


def _make_resolver(provider_name: str):
    def _resolve() -> OpenAIImageCredentials:
        for entry in resolve_provider_entries():
            if entry.name == provider_name:
                return OpenAIImageCredentials(entry.enable, entry.api_key, entry.base_url)
        return OpenAIImageCredentials(False, "", "")

    return _resolve


def sync_openai_image_providers() -> None:
    """按当前配置重挂供应商通道: 先清除本层历史注入, 再按启用项 register_binding。"""
    for model_name, channel_name in _REGISTERED:
        channel_registry.unregister(model_name, channel_name)
    _REGISTERED.clear()

    seen_names: set[str] = set()
    total = 0
    for entry in resolve_provider_entries():
        if not entry.enable:
            continue
        if entry.name in seen_names:
            logger.warning(f"[OpenAIImage] 供应商名重复, 已跳过后者: {entry.name}")
            continue
        seen_names.add(entry.name)
        channel = OpenAIImageChannel(entry.name, credentials_resolver=_make_resolver(entry.name))
        for b in entry.models:
            channel_registry.register_binding(b.model_real_name, channel, vendor_model=b.model_id)
            _REGISTERED.append((b.model_real_name, entry.name))
            total += 1
    logger.info(f"[OpenAIImage] 供应商池已同步: {len(seen_names)} 家启用, {total} 条模型绑定")


__all__ = ["sync_openai_image_providers", "resolve_provider_entries", "ProviderEntry"]
