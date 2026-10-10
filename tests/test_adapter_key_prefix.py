"""Adapter 通道把当前凭证前 6 位交给统计。本地 ComfyUI 没有供应商 key。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pytest import MonkeyPatch

from RH_ComfyUI.models.bridge import AdapterChannel
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
    assert FishAudioAdapter().audit_key_prefix() == "fish-S"
    assert RHAppAdapter().audit_key_prefix() == "rhapp-"
    assert MIMOAdapter().audit_key_prefix() == "mimo-K"
    assert MiniMaxAdapter().audit_key_prefix() == "mm-key"
    assert SeedreamAdapter().audit_key_prefix() == "ark-ke"
    assert GPTImage2Adapter().audit_key_prefix() == "sk-gpt"
    assert TxAiartAdapter().audit_key_prefix() == "AKIDtx"


def test_empty_key_stays_blank(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, FishAudioAPI, "api_key", "")
    assert FishAudioAdapter().audit_key_prefix() == ""


def test_comfyui_prefix_only_on_runninghub(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, ComfyUIAPI, "api_key", "rhkey12345")
    _pin(monkeypatch, ComfyUIAPI, "is_runninghub", "")
    assert ComfyUIAdapter().audit_key_prefix() == ""
    monkeypatch.setattr(ComfyUIAPI, "is_runninghub", property(lambda self: True))
    assert ComfyUIAdapter().audit_key_prefix() == "rhkey1"


def test_adapter_channel_delegates_prefix(monkeypatch: MonkeyPatch) -> None:
    _pin(monkeypatch, FishAudioAPI, "api_key", "fish-SECRET")
    adapter = FishAudioAdapter()
    channel = AdapterChannel("fishaudio")
    monkeypatch.setattr(channel, "_adapter", lambda: adapter)
    assert channel.audit_key_prefix() == "fish-S"


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


def test_backfill_skips_when_marker_exists(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "key_prefix_backfill.done"
    marker.write_text("done", encoding="utf-8")
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)

    async def _boom(**_kwargs: object) -> list[tuple[int, str, str]]:
        raise AssertionError("marker exists")

    monkeypatch.setattr(
        "RH_ComfyUI.utils.database.models.RHComfyuiTaskRecord.list_blank_key_prefix_page",
        staticmethod(_boom),
    )
    assert asyncio.run(backfill.backfill_historical_key_prefixes()) == 0


def test_backfill_pages_and_writes_marker(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    from RH_ComfyUI.utils.database import key_prefix_backfill as backfill

    marker = tmp_path / "nested" / "key_prefix_backfill.done"
    monkeypatch.setattr(backfill, "_marker_path", lambda: marker)
    monkeypatch.setattr(backfill, "collect_audit_prefixes", lambda: {"fishaudio": "fish-S"})
    seen: list[dict[int, str]] = []

    async def _page(*, after_id: int, limit: int) -> list[tuple[int, str, str, str]]:
        del limit
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
    assert seen == [{1: "fish-S"}]
    assert marker.read_text(encoding="utf-8") == "done"
