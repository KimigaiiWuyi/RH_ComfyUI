"""Fish Audio 可启用档位。

配置「启用的 Fish Audio 模型」与注册表 name 共用这份名单,
上游请求的 model header 也是同一个字符串。
"""

from __future__ import annotations

from typing import Literal
from dataclasses import dataclass

FishTask = Literal["speech", "asr"]

LEGACY_FISH_TTS = "fish_tts"
LEGACY_FISH_ASR = "fish_asr"
DEFAULT_TTS_MODEL = "s2.1-pro"
DEFAULT_ASR_MODEL = "transcribe-1"


@dataclass(frozen=True)
class FishModelSpec:
    name: str
    display_name: str
    task: FishTask
    description: str
    priority: int
    free: bool = False


FISH_TTS_MODELS: tuple[FishModelSpec, ...] = (
    FishModelSpec(
        name="s2-pro",
        display_name="Fish Audio S2 Pro",
        task="speech",
        description="上一代 S2 口播",
        priority=82,
    ),
    FishModelSpec(
        name="s2.1-pro",
        display_name="Fish Audio S2.1 Pro",
        task="speech",
        description="多语言口播首选",
        priority=85,
    ),
    FishModelSpec(
        name="s2.1-pro-free",
        display_name="Fish Audio S2.1 Pro Free",
        task="speech",
        description="与 S2.1 Pro 同质量的免费档",
        priority=70,
        free=True,
    ),
    FishModelSpec(
        name="drama-3-preview",
        display_name="Fish Audio Drama 3 Preview",
        task="speech",
        description="对白向预览模型",
        priority=78,
    ),
)

FISH_ASR_MODELS: tuple[FishModelSpec, ...] = (
    FishModelSpec(
        name="transcribe-1",
        display_name="Fish Audio 语音识别",
        task="asr",
        description="多语言转写字幕",
        priority=80,
    ),
    FishModelSpec(
        name="transcribe-1-pro",
        display_name="Fish Audio 语音识别 Pro",
        task="asr",
        description="多人对话转写,保留说话人与情绪提示",
        priority=78,
    ),
)

FISH_MODEL_NAMES: list[str] = [spec.name for spec in (*FISH_TTS_MODELS, *FISH_ASR_MODELS)]
TTS_MODEL_NAMES: frozenset[str] = frozenset(spec.name for spec in FISH_TTS_MODELS)
ASR_MODEL_NAMES: frozenset[str] = frozenset(spec.name for spec in FISH_ASR_MODELS)


def expand_legacy_fish_enabled(names: list[str], legacy_tier: str) -> list[str]:
    """fish_tts → 旧档位(非法含 s1 则 s2.1-pro);fish_asr → transcribe-1。无旧名则原样返回。"""
    if LEGACY_FISH_TTS not in names and LEGACY_FISH_ASR not in names:
        return names
    out: list[str] = []
    for name in names:
        if name == LEGACY_FISH_TTS:
            mapped = legacy_tier if legacy_tier in TTS_MODEL_NAMES else DEFAULT_TTS_MODEL
            if mapped not in out:
                out.append(mapped)
        elif name == LEGACY_FISH_ASR:
            if DEFAULT_ASR_MODEL not in out:
                out.append(DEFAULT_ASR_MODEL)
        elif name and name not in out:
            out.append(name)
    return out


def migrate_stored_fish_enabled() -> None:
    """把已存的 fish_tts / fish_asr 写成档位名,让控制台勾选和运行时一致。"""
    from .comfyui_config import SERVICE_CONFIG

    if "FishAudio_Enabled_Models" not in SERVICE_CONFIG.config:
        return
    item = SERVICE_CONFIG.config["FishAudio_Enabled_Models"]
    raw = item.data
    names: list[str] = []
    if isinstance(raw, str):
        if raw.strip():
            names.append(raw.strip())
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
    else:
        return
    if LEGACY_FISH_TTS not in names and LEGACY_FISH_ASR not in names:
        return
    legacy = ""
    if "FishAudio_Model" in SERVICE_CONFIG.config:
        stored = SERVICE_CONFIG.config["FishAudio_Model"].data
        if isinstance(stored, str):
            legacy = stored
    item.data = expand_legacy_fish_enabled(names, legacy)
    SERVICE_CONFIG.write_config()
