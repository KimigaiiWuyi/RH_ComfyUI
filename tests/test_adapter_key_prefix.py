"""Adapter 通道把当前凭证脱敏串交给统计。本地 ComfyUI 没有供应商 key。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pytest import MonkeyPatch

from RH_ComfyUI.models.bridge import AdapterChannel
from RH_ComfyUI.utils.audit_key import mask_api_key
from RH_ComfyUI.utils.backends.mimo.api import MIMOAPI
from RH_ComfyUI.utils.backends.rh_app.api import RHAppAPI
from RH_ComfyUI.utils.backends.comfyui.api import ComfyUIAPI
from RH_ComfyUI.utils.backends.minimax.api import MiniMaxAPI
from RH_ComfyUI.utils.backends.seedream.api import SeedreamAPI
from RH_ComfyUI.utils.backends.tx_aiart.api import TxAiartAPI
from RH_ComfyUI.utils.backends.fishaudio.api import FishAudioAPI
from RH_ComfyUI.utils.backends.mimo.executor import MIMOAdapter
from RH_ComfyUI.utils.backends.gpt_image2.api import GPTImage2API
from RH_ComfyUI.utils.backends.rh_app.executor import RHAppAdapter
from RH_ComfyUI.utils.backends.comfyui.executor import ComfyUIAdapter
from RH_ComfyUI.utils.backends.minimax.executor import MiniMaxAdapter
from RH_ComfyUI.utils.backends.seedream.executor import SeedreamAdapter
from RH_ComfyUI.utils.backends.tx_aiart.executor import TxAiartAdapter
from RH_ComfyUI.utils.backends.fishaudio.executor import FishAudioAdapter
from RH_ComfyUI.utils.backends.gpt_image2.executor import GPTImage2Adapter


def _pin(monkeypatch: MonkeyPatch, cls: type, name: str, value: str) -> None:
    monkeypatch.setattr(cls, name, property(lambda self: value))


def test_vendor_adapters_record_key_prefix(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, FishAudioAPI, "api_key", "fish-SECRET")
    _pin(monkeypatch, RHAppAPI, "api_key", "rhapp-KEY")
    _pin(monkeypatch, MIMOAPI, "api_key", "mimo-KEY12")
    _pin(monkeypatch, MiniMaxAPI, "api_key", "mm-key-99")
    _pin(monkeypatch, SeedreamAPI, "api_key", "ark-key-1")
    _pin(monkeypatch, GPTImage2API, "api_key", "sk-gptimage")
    _pin(monkeypatch, TxAiartAPI, "secret_id", "AKIDtxsecret")
    assert FishAudioAdapter().audit_key_prefix() == mask_api_key("fish-SECRET")
    assert RHAppAdapter().audit_key_prefix() == mask_api_key("rhapp-KEY")
    assert MIMOAdapter().audit_key_prefix() == mask_api_key("mimo-KEY12")
    assert MiniMaxAdapter().audit_key_prefix() == mask_api_key("mm-key-99")
    assert SeedreamAdapter().audit_key_prefix() == mask_api_key("ark-key-1")
    assert GPTImage2Adapter().audit_key_prefix() == mask_api_key("sk-gptimage")
    assert TxAiartAdapter().audit_key_prefix() == mask_api_key("AKIDtxsecret")


def test_empty_key_stays_blank(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, FishAudioAPI, "api_key", "")
    assert FishAudioAdapter().audit_key_prefix() == ""


def test_comfyui_prefix_only_on_runninghub(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, ComfyUIAPI, "api_key", "rhkey12345")
    _pin(monkeypatch, ComfyUIAPI, "is_runninghub", "")
    assert ComfyUIAdapter().audit_key_prefix() == ""
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))
    assert ComfyUIAdapter().audit_key_prefix() == mask_api_key("rhkey12345")


def test_adapter_channel_delegates_prefix(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, FishAudioAPI, "api_key", "fish-SECRET")
    adapter = FishAudioAdapter()
    channel = AdapterChannel("fishaudio")
    monkeypatch.setattr(channel, "_adapter", lambda: adapter)
    assert channel.audit_key_prefix() == mask_api_key("fish-SECRET")


def test_unknown_backend_prefix_empty() -> None:
    assert AdapterChannel("no-such-backend").audit_key_prefix() == ""


def test_historical_row_uses_provider_then_backend() -> None:
    from RH_ComfyUI.utils.database.key_prefix_backfill import prefix_for_row

    prefixes = {
        "fishaudio": "fish-S",
        "comfyui": "rhkey1",
        "ark": "ark-ke",
        "gemini-ai-studio": "AIzaKE",
        "gemini-vertex": "projxy",
    }
    assert prefix_for_row("fishaudio", "fishaudio", prefixes) == "fish-S"
    assert prefix_for_row("fishaudio", "", prefixes) == "fish-S"
    assert prefix_for_row("comfyui", "comfyui", prefixes) == "rhkey1"
    assert prefix_for_row("seedance", "ark", prefixes) == "ark-ke"
    assert prefix_for_row("gemini-image", "gemini-ai-studio", prefixes) == "AIzaKE"
    assert prefix_for_row("gemini-image", "gemini-vertex", prefixes) == "projxy"
    assert prefix_for_row("gemini-image", "gemini", prefixes) == ""
    assert prefix_for_row("fishaudio", "other-vendor", prefixes) == ""
    assert prefix_for_row("fishaudio", "other-vendor", prefixes, "fishaudio") == ""
    assert prefix_for_row("seedance", "", prefixes) == ""
    slot_prefixes = {"seedance": "sd-key", "ark": "ark-ke", "gateway_slot1_seedance": "gwkey1"}
    assert prefix_for_row("seedance", "", slot_prefixes, "gateway_slot1_seedance") == "gwkey1"
    assert prefix_for_row("seedance", "", slot_prefixes, "ark") == "ark-ke"
    assert prefix_for_row("seedance", "", slot_prefixes, "") == "sd-key"
    assert prefix_for_row("seedance", "ark", slot_prefixes, "gateway_slot1_seedance") == "ark-ke"
    long_alpha = "gateway_slot2_alpha_channel_name_extra"
    assert len(long_alpha) > 32
    clip = long_alpha[:32]
    slot2 = {long_alpha: "slot2*****KEYAA", "gateway_slot2_beta": "slot2*****KEYAA"}
    assert prefix_for_row("gemini-image", clip, slot2) == "slot2*****KEYAA"
    assert prefix_for_row("gemini-image", clip, slot2, long_alpha) == "slot2*****KEYAA"
    twin_a = ("z" * 32) + "aaa"
    twin_b = ("z" * 32) + "bbb"
    twins = {twin_a: "key-A*****AAAAA", twin_b: "key-B*****BBBBB"}
    assert prefix_for_row("gpt-image-2", "z" * 32, twins) == ""
    assert prefix_for_row("gpt-image-2", "z" * 32, twins, twin_a) == "key-A*****AAAAA"
    slot2_live = {"gateway_slot2_beta": "slot2*****KEYAA"}
    assert prefix_for_row("gpt-image-2", "gateway_slot2_alpha_old", slot2_live) == "slot2*****KEYAA"
    assert prefix_for_row("gpt-image-2", "gateway_slot2_gamma", slot2_live) == "slot2*****KEYAA"
    slot3_live = {"gateway_slot3_beta": "slot3*****KEYBB"}
    assert prefix_for_row("seedream", "gateway_slot3_alpha_old", slot3_live) == "slot3*****KEYBB"
    alpha_clip = long_alpha[:32]
    assert len(alpha_clip) == 32
    assert prefix_for_row("gpt-image-2", alpha_clip, {long_alpha: "slot2*****KEYAA"}) == "slot2*****KEYAA"
    mixed = {
        "gateway_slot1_alpha": "slot1*****MASKA",
        "gateway_slot1_beta": "slot1x",
    }
    assert prefix_for_row("seedance", "gateway_slot1_alpha_old", mixed) == "slot1*****MASKA"
    rh_only = {"rh_app": "rhapp*****XXXXX"}
    assert prefix_for_row("comfyui", "comfyui", rh_only, "runninghub") == "rhapp*****XXXXX"
    assert prefix_for_row("comfyui", "comfyui", rh_only, "") == "rhapp*****XXXXX"
    slots = {
        "gateway_slot1_alpha": "slot1*****MASKA",
        "gateway_slot2_beta": "slot2*****KEYAA",
    }
    assert prefix_for_row("seedance", "gateway", slots) == ""


def test_gateway_slot_stem_strips_family_suffix() -> None:
    from RH_ComfyUI.utils.database.key_prefix_backfill import _gateway_slot_stem

    assert _gateway_slot_stem("gateway_slot2_gpt_image_2") == "gateway_slot2"
    assert _gateway_slot_stem("gateway_slot10_alpha") == "gateway_slot10"
    assert _gateway_slot_stem("seedance") == ""


def _patch_counts(monkeypatch: MonkeyPatch, values: list[int]) -> None:
    leftover = list(values)

    async def _count() -> int:
        if leftover:
            return leftover.pop(0)
        return 0

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.count_blank_key_prefix",
        staticmethod(_count),
    )


async def _boom_page(**_kwargs: object) -> list[tuple[int, str, str, str]]:
    raise AssertionError("probe decided not to page")


def test_backfill_skips_when_count_zero(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    marker.write_text("remaining=0", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    _patch_counts(monkeypatch, [0])
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom_page),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert marker.read_text(encoding="utf-8") == "remaining=0"


def test_backfill_new_empty_rows_do_not_repage(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """remaining 已记下后,本地无钥新行不再抬高 COUNT,不翻页。"""
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    marker.write_text("remaining=0", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-*****CRET"})
    _patch_counts(monkeypatch, [0])
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom_page),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert marker.read_text(encoding="utf-8") == "remaining=0"


def test_backfill_first_scan_pages_empty_when_legacy_count_zero(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-*****CRET"})
    _patch_counts(monkeypatch, [0, 0])
    seen: list[dict[int, str]] = []
    flags: list[bool] = []

    async def _page(*, after_id: int, limit: int, include_empty: bool = False) -> list[tuple[int, str, str, str]]:
        del limit
        flags.append(include_empty)
        if after_id == 0:
            return [(9, "fishaudio", "fishaudio", "")]
        return []

    async def _apply(updates: dict[int, str]) -> int:
        seen.append(updates)
        return len(updates)

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_page),
    )
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.apply_key_prefix_batch",
        staticmethod(_apply),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 1
    assert flags == [True, True]
    assert seen == [{9: "fish-*****CRET"}]
    assert marker.read_text(encoding="utf-8") == "remaining=0"


def test_backfill_skips_when_count_within_remaining(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    marker.write_text("remaining=23", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    _patch_counts(monkeypatch, [23])
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom_page),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert marker.read_text(encoding="utf-8") == "remaining=23"


def test_backfill_lowers_remaining_without_scan(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    marker.write_text("remaining=23", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    _patch_counts(monkeypatch, [20])
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom_page),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert marker.read_text(encoding="utf-8") == "remaining=20"


def test_backfill_old_done_with_count_runs_once(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    marker.write_text("done", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-*****CRET"})
    _patch_counts(monkeypatch, [1, 1])
    seen: list[dict[int, str]] = []

    async def _page(*, after_id: int, limit: int, include_empty: bool = False) -> list[tuple[int, str, str, str]]:
        del limit
        assert include_empty is True
        if after_id == 0:
            return [(9, "fishaudio", "other-vendor", "")]
        return []

    async def _apply(updates: dict[int, str]) -> int:
        seen.append(updates)
        return len(updates)

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_page),
    )
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.apply_key_prefix_batch",
        staticmethod(_apply),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert seen == []
    assert marker.read_text(encoding="utf-8") == "remaining=1"


def test_backfill_reruns_when_count_exceeds_remaining(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    marker.write_text("remaining=0", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-*****CRET"})
    _patch_counts(monkeypatch, [1, 0])
    seen: list[dict[int, str]] = []

    async def _page(*, after_id: int, limit: int, include_empty: bool = False) -> list[tuple[int, str, str, str]]:
        del limit
        assert include_empty is False
        if after_id == 0:
            return [(9, "fishaudio", "fishaudio", "")]
        return []

    async def _apply(updates: dict[int, str]) -> int:
        seen.append(updates)
        return len(updates)

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_page),
    )
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.apply_key_prefix_batch",
        staticmethod(_apply),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 1
    assert seen == [{9: "fish-*****CRET"}]
    assert marker.read_text(encoding="utf-8") == "remaining=0"


def test_backfill_first_scan_no_prefixes_does_not_write_marker(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {})
    _patch_counts(monkeypatch, [0])
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom_page),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert not marker.is_file()


def test_backfill_no_prefixes_does_not_write_marker(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {})
    _patch_counts(monkeypatch, [12])
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom_page),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0
    assert not marker.is_file()


def test_backfill_pages_and_writes_marker(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "nested" / "key_mask_head_tail_v2.done"
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-*****CRET"})
    _patch_counts(monkeypatch, [2, 1])
    seen: list[dict[int, str]] = []

    async def _page(*, after_id: int, limit: int, include_empty: bool = False) -> list[tuple[int, str, str, str]]:
        del limit
        assert include_empty is True
        if after_id == 0:
            return [
                (1, "fishaudio", "fishaudio", ""),
                (2, "fishaudio", "other-vendor", "gateway_slot1_seedance"),
            ]
        return []

    async def _apply(updates: dict[int, str]) -> int:
        seen.append(updates)
        return len(updates)

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_page),
    )
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.apply_key_prefix_batch",
        staticmethod(_apply),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 1
    assert seen == [{1: "fish-*****CRET"}]
    assert marker.read_text(encoding="utf-8") == "remaining=1"


def test_backfill_rewrites_legacy_six_char_prefix(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_mask_head_tail_v2.done"
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-*****CRET"})
    _patch_counts(monkeypatch, [1, 0])
    seen: list[dict[int, str]] = []

    async def _page(*, after_id: int, limit: int, include_empty: bool = False) -> list[tuple[int, str, str, str]]:
        del limit
        assert include_empty is True
        if after_id == 0:
            return [(9, "fishaudio", "fishaudio", "")]
        return []

    async def _apply(updates: dict[int, str]) -> int:
        seen.append(updates)
        return len(updates)

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_page),
    )
    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.apply_key_prefix_batch",
        staticmethod(_apply),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 1
    assert seen == [{9: "fish-*****CRET"}]
    assert marker.read_text(encoding="utf-8") == "remaining=0"


def test_heal_predicate_excludes_empty_prefix() -> None:
    from sqlalchemy.dialects import sqlite

    from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord

    sql = str(
        RHComfyuiTaskRecord._needs_key_prefix_heal().compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "is not null" in sql
    assert "*****" in sql
    assert "!= ''" in sql or "<> ''" in sql or '!= ""' in sql


def test_parse_remaining_marker() -> None:
    from RH_ComfyUI.utils.database.key_prefix_backfill import parse_remaining_marker

    assert parse_remaining_marker("remaining=23") == 23
    assert parse_remaining_marker("remaining=0\n") == 0
    assert parse_remaining_marker("done") is None
    assert parse_remaining_marker("") is None
    assert parse_remaining_marker("remaining=-1") is None
