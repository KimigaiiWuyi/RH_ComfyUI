"""供应商家族:NodeDef.provider 是家族 id,channel.name 是实例 id。

约定(与外部插件已落地命名兼容,不改实例名):
- 一家一条通道:名字等于家族(aifoundation / azure / dashscope);
- 一家多实例:``{家族}_{限定}``,如 gateway_slot1_seedance。

钉扎仍精确匹配实例名;本模块只用于节点锁死家族时的候选过滤。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .channel import ChannelBinding, ProviderChannel


def channel_name_in_family(name: str, family: str) -> bool:
    """``name`` 属于家族:相等,或以 ``{family}_`` 开头。"""
    fam = family.strip()
    inst = name.strip()
    if not fam or not inst:
        return False
    return inst == fam or inst.startswith(fam + "_")


def lock_bindings_to_provider_family(
    family: str,
    builtins: Mapping[str, ProviderChannel],
    external: Sequence[ChannelBinding],
    *,
    builtin_vendor_model: str | None,
) -> list[ChannelBinding]:
    """节点锁死供应商家族。

    内置表命中该家族 key → 只留那条内置(与 ``provider=ark`` 历史行为一致);
    否则按家族从外部绑定里收,保留各 binding 自带的 vendor_model。
    """
    fam = family.strip()
    if not fam:
        return []
    if fam in builtins:
        return [ChannelBinding(channel=builtins[fam], vendor_model=builtin_vendor_model)]
    return [b for b in external if channel_name_in_family(b.channel.name, fam)]


__all__ = [
    "channel_name_in_family",
    "lock_bindings_to_provider_family",
]
