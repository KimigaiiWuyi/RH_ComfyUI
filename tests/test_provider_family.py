"""NodeDef.provider 是家族 id,不是 channel.name 精确相等。"""

from dataclasses import replace

import pytest

from RH_ComfyUI.core import channel_registry
from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.models.video.defs import Wan30Def, MiniMaxH3Def, Seedance2Def, HappyHorse11Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.channels.family import channel_name_in_family, lock_bindings_to_provider_family
from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding
from RH_ComfyUI.models.video.overrides import (
    Wan30VideoModel,
    SeedanceVideoModel,
    MiniMaxH3VideoModel,
    HappyHorseVideoModel,
)


def test_channel_name_in_family_rules():
    assert channel_name_in_family("gateway", "gateway") is True
    assert channel_name_in_family("gateway_slot1_seedance", "gateway") is True
    assert channel_name_in_family("aifoundation", "aifoundation") is True
    assert channel_name_in_family("aifoundation_extra", "aifoundation") is True
    assert channel_name_in_family("ark", "gateway") is False
    assert channel_name_in_family("gate", "gateway") is False
    assert channel_name_in_family("gateway_slot1_seedance", "ark") is False
    assert channel_name_in_family("", "gateway") is False
    assert channel_name_in_family("gateway", "") is False


def test_lock_hits_builtin_and_ignores_externals():
    ark = LocalChannel("ark")
    slot = LocalChannel("gateway_slot1_seedance")
    bindings = lock_bindings_to_provider_family(
        "ark",
        {"ark": ark},
        [ChannelBinding(slot, vendor_model="dreamina")],
        builtin_vendor_model="doubao-x",
    )
    assert len(bindings) == 1
    assert bindings[0].channel is ark
    assert bindings[0].vendor_model == "doubao-x"


def test_lock_collects_family_externals_keeps_vendor_model():
    ark = LocalChannel("ark")
    s1 = LocalChannel("gateway_slot1_seedance")
    s2 = LocalChannel("gateway_slot2_seedance")
    legacy = LocalChannel("gateway")
    azure = LocalChannel("azure")
    bindings = lock_bindings_to_provider_family(
        "gateway",
        {"ark": ark, "runninghub": LocalChannel("runninghub")},
        [
            ChannelBinding(s1, vendor_model="dreamina-a"),
            ChannelBinding(s2, vendor_model="dreamina-b"),
            ChannelBinding(legacy, vendor_model="dreamina-old"),
            ChannelBinding(azure, vendor_model="az"),
        ],
        builtin_vendor_model="unused",
    )
    by_name = {b.channel.name: b.vendor_model for b in bindings}
    assert by_name == {
        "gateway_slot1_seedance": "dreamina-a",
        "gateway_slot2_seedance": "dreamina-b",
        "gateway": "dreamina-old",
    }


def test_lock_empty_family_returns_empty():
    assert (
        lock_bindings_to_provider_family(
            "  ",
            {"ark": LocalChannel("ark")},
            [ChannelBinding(LocalChannel("gateway"), vendor_model="x")],
            builtin_vendor_model=None,
        )
        == []
    )


def test_seedance_intl_family_includes_slots_not_ark():
    channel_registry.clear()
    node = replace(
        Seedance2Def.node_def(),
        name="seedance2_intl",
        provider="gateway",
        backend_models={"gateway": "dreamina-seedance-2.0"},
        backend_model="dreamina-seedance-2.0",
    )
    model = SeedanceVideoModel(node)
    channel_registry.register_binding(
        "seedance2_intl", LocalChannel("gateway_slot1_seedance"), vendor_model="dreamina-seedance-2.0"
    )
    channel_registry.register_binding(
        "seedance2_intl", LocalChannel("gateway_slot2_seedance"), vendor_model="dreamina-seedance-2.0"
    )
    channel_registry.register_binding("seedance2_intl", LocalChannel("azure"), vendor_model="nope")
    names = [b.channel.name for b in model.channel_bindings()]
    assert names == ["gateway_slot1_seedance", "gateway_slot2_seedance"]
    assert {b.vendor_model for b in model.channel_bindings()} == {"dreamina-seedance-2.0"}


def test_seedance_intl_pin_is_instance_name_not_family():
    channel_registry.clear()
    node = replace(
        Seedance2Def.node_def(),
        name="seedance2_intl",
        provider="gateway",
        backend_models={"gateway": "dreamina-seedance-2.0"},
    )
    model = SeedanceVideoModel(node)
    channel_registry.register_binding(
        "seedance2_intl", LocalChannel("gateway_slot1_seedance"), vendor_model="dreamina-seedance-2.0"
    )
    channel_registry.register_binding(
        "seedance2_intl", LocalChannel("gateway_slot2_seedance"), vendor_model="dreamina-seedance-2.0"
    )
    pinned = model.bindings_for_request(
        GenerationRequest(task_type=TaskType.VIDEO, prompt="a", channel="gateway_slot1_seedance")
    )
    assert [b.channel.name for b in pinned] == ["gateway_slot1_seedance"]
    with pytest.raises(ValidationError, match="gateway"):
        model.bindings_for_request(GenerationRequest(task_type=TaskType.VIDEO, prompt="a", channel="gateway"))


def test_happyhorse_locked_to_gateway_family():
    channel_registry.clear()
    node = replace(HappyHorse11Def.node_def(), provider="gateway")
    model = HappyHorseVideoModel(node)
    channel_registry.register_binding("happyhorse1.1", LocalChannel("gateway_slot1_happyhorse"))
    channel_registry.register_binding("happyhorse1.1", LocalChannel("dashscope-extra"))
    names = [b.channel.name for b in model.channel_bindings()]
    assert names == ["gateway_slot1_happyhorse"]


def test_h3_and_wan_locked_to_gateway_family():
    channel_registry.clear()
    h3 = MiniMaxH3VideoModel(replace(MiniMaxH3Def.node_def(), provider="gateway"))
    wan = Wan30VideoModel(replace(Wan30Def.node_def(), provider="gateway"))
    channel_registry.register_binding(
        "minimax_h3", LocalChannel("gateway_slot1_minimax_h3"), vendor_model="MiniMax-H3"
    )
    channel_registry.register_binding(
        "wan3.0", LocalChannel("gateway_slot2_wan30"), vendor_model="wan3.0-video"
    )
    assert [b.channel.name for b in h3.channel_bindings()] == ["gateway_slot1_minimax_h3"]
    assert [b.channel.name for b in wan.channel_bindings()] == ["gateway_slot2_wan30"]
    assert h3.channel_bindings()[0].vendor_model == "MiniMax-H3"
    assert wan.channel_bindings()[0].vendor_model == "wan3.0-video"
