"""Gemini 3.8 Flash-Lite TTS 积分。

只计 Standard。1 美元 = 100 积分,向上取整,最少 1。
2026-12-31 及以前:输入文本 50 积分/百万 token,音频 600。
2027-01-01 起:输入 100,音频 1200。
预扣不知道时长,按 4 字/秒估算,音频 token 封顶 16384。
"""

from __future__ import annotations

from datetime import date

from .gemini_image_usage import find_usage_metadata

_PRICE_STEP = date(2027, 1, 1)
_INPUT_BEFORE = 50
_AUDIO_BEFORE = 600
_INPUT_AFTER = 100
_AUDIO_AFTER = 1200
_AUDIO_TOKENS_PER_SECOND = 25
_MAX_AUDIO_TOKENS = 16384
_MILLION = 1_000_000


def gemini_tts_rates(today: date | None = None) -> tuple[int, int]:
    """返回 (输入积分/百万 token, 音频积分/百万 token)。"""
    when = date.today() if today is None else today
    if when >= _PRICE_STEP:
        return (_INPUT_AFTER, _AUDIO_AFTER)
    return (_INPUT_BEFORE, _AUDIO_BEFORE)


def _ceil_points(numer: int) -> int:
    if numer <= 0:
        return 1
    return max(1, (numer + _MILLION - 1) // _MILLION)


def estimate_gemini_tts_points(text: str | None, *, today: date | None = None) -> int:
    """预扣。空文本也是 1 积分。"""
    raw = text or ""
    seconds = max(1, (len(raw) + 3) // 4)
    audio_tokens = min(_MAX_AUDIO_TOKENS, seconds * _AUDIO_TOKENS_PER_SECOND)
    text_tokens = len(raw)
    input_rate, audio_rate = gemini_tts_rates(today)
    numer = text_tokens * input_rate + audio_tokens * audio_rate
    return _ceil_points(numer)


def _nonneg_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = int(value)
    return number if number > 0 else 0


def _field_int(blob: dict[str, object], key: str) -> int:
    if key not in blob:
        return 0
    return _nonneg_int(blob[key])


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _audio_tokens(meta: dict[str, object]) -> int:
    """有 AUDIO 明细就用它,否则用 candidates_token_count。"""
    if "candidates_tokens_details" in meta:
        details = meta["candidates_tokens_details"]
        if isinstance(details, list):
            total = 0
            found = False
            for item in details:
                blob = _as_dict(item)
                if blob is None or "modality" not in blob:
                    continue
                modality = blob["modality"]
                if not isinstance(modality, str):
                    continue
                token = modality.rsplit(".", 1)[-1].upper()
                if token != "AUDIO":
                    continue
                count = blob["token_count"] if "token_count" in blob else 0
                total += _nonneg_int(count)
                found = True
            if found:
                return total
    return _field_int(meta, "candidates_token_count")


def settle_gemini_tts_points(usage: dict[str, object], *, today: date | None = None) -> int | None:
    """按 usage_metadata 实扣。两项都是 0 时返回 None,维持预扣。"""
    meta = find_usage_metadata(usage)
    if meta is None:
        return None
    prompt_tokens = _field_int(meta, "prompt_token_count")
    audio_tokens = _audio_tokens(meta)
    if prompt_tokens == 0 and audio_tokens == 0:
        return None
    input_rate, audio_rate = gemini_tts_rates(today)
    numer = prompt_tokens * input_rate + audio_tokens * audio_rate
    return _ceil_points(numer)


__all__ = [
    "estimate_gemini_tts_points",
    "gemini_tts_rates",
    "settle_gemini_tts_points",
]
