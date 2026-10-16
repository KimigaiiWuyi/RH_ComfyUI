"""Gemini 3.8 Flash-Lite TTS 预置音色。单说话人,默认 Kore。"""

from __future__ import annotations

GEMINI_TTS_UPSTREAM_MODEL = "gemini-3.8-flash-lite-tts"
GEMINI_TTS_DEFAULT_VOICE = "Kore"

GEMINI_TTS_VOICES: tuple[str, ...] = (
    "Zephyr",
    "Puck",
    "Charon",
    "Kore",
    "Fenrir",
    "Leda",
    "Orus",
    "Aoede",
    "Callirrhoe",
    "Autonoe",
    "Enceladus",
    "Iapetus",
    "Umbriel",
    "Algieba",
    "Despina",
    "Erinome",
    "Algenib",
    "Rasalgethi",
    "Laomedeia",
    "Achernar",
    "Alnilam",
    "Schedar",
    "Gacrux",
    "Pulcherrima",
    "Achird",
    "Zubenelgenubi",
    "Vindemiatrix",
    "Sadachbia",
    "Sadaltager",
    "Sulafat",
)

__all__ = [
    "GEMINI_TTS_DEFAULT_VOICE",
    "GEMINI_TTS_UPSTREAM_MODEL",
    "GEMINI_TTS_VOICES",
]
