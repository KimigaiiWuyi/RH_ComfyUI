"""Qwen-Audio TTS 积分。

1 元 = 100 积分,向上取整,最少 1。
3.0 flash / plus 只计输入字符(汉字 2,其余 1),输出不计。
北京:flash 1 元/万字,plus 1.4 元/万字。
国际站(主机名含 dashscope-intl):flash 1.12413,plus 1.49884。
3.1 按 token。北京输入 1.5 元/百万、输出 12 元/百万;
国际站 1.677 与 13.636。预扣没有 usage,时长按 4 字/秒估算。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .qwen_tts_voices import QWEN_TTS_31, QWEN_TTS_PLUS, QWEN_TTS_FLASH
from ..backends.dashscope.config import DASHSCOPE_DEFAULT_BASE_URL, dashscope_credentials

QWEN_INSTRUCTION_LIMIT = 100

# 每万字的微元(1e-6 元)。积分 = ceil(字符 * 微元 / 1e8)。
_CHAR_MICRO_PER_10K: dict[tuple[str, str], int] = {
    (QWEN_TTS_FLASH, "cn"): 1_000_000,
    (QWEN_TTS_FLASH, "intl"): 1_124_130,
    (QWEN_TTS_PLUS, "cn"): 1_400_000,
    (QWEN_TTS_PLUS, "intl"): 1_498_840,
}
# 每百万 token 的毫积分(1e-3 积分)。北京 150 / 1200,国际 167.7 / 1363.6。
_TOKEN_MILLI_PER_MILLION: dict[str, tuple[int, int]] = {
    "cn": (150_000, 1_200_000),
    "intl": (167_700, 1_363_600),
}
_CHAR_DIVISOR = 100_000_000
_TOKEN_DIVISOR = 1_000_000_000


def qwen_tts_region(base_url: str) -> str:
    host = urlsplit(base_url).netloc.lower()
    if "dashscope-intl" in host:
        return "intl"
    return "cn"


def qwen_weighted_chars(text: str) -> int:
    """汉字(含扩展 A、兼容汉字)计 2,其余计 1。"""
    total = 0
    for ch in text:
        code = ord(ch)
        if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            total += 2
        else:
            total += 1
    return total


def _resolved_base(base_url: str | None) -> str:
    if base_url is not None and base_url.strip():
        return base_url.strip()
    creds = dashscope_credentials()
    return creds.base_url or DASHSCOPE_DEFAULT_BASE_URL


def _char_points(chars: int, micro_per_10k: int) -> int:
    if chars <= 0:
        return 1
    return max(1, (chars * micro_per_10k + _CHAR_DIVISOR - 1) // _CHAR_DIVISOR)


def _token_points(input_tokens: int, output_tokens: int, region: str) -> int:
    rates = _TOKEN_MILLI_PER_MILLION[region]
    numer = input_tokens * rates[0] + output_tokens * rates[1]
    if numer <= 0:
        return 1
    return max(1, (numer + _TOKEN_DIVISOR - 1) // _TOKEN_DIVISOR)


def estimate_qwen_tts_points(model: str, text: str | None, *, base_url: str | None = None) -> int:
    raw = text or ""
    region = qwen_tts_region(_resolved_base(base_url))
    if model == QWEN_TTS_31:
        # 预扣没有真实时长。输出按 12.5 token/字,且至少 1 秒(50 token)。
        output_tokens = max(50, (len(raw) * 25) // 2)
        return _token_points(len(raw), output_tokens, region)
    micro = _CHAR_MICRO_PER_10K.get((model, region))
    if micro is None:
        return 1
    return _char_points(qwen_weighted_chars(raw), micro)


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _nonneg_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    if number < 0:
        return None
    return number


def _find_usage(payload: object) -> dict[str, object] | None:
    seen: set[int] = set()

    def walk(node: object) -> dict[str, object] | None:
        blob = _as_dict(node)
        if blob is None:
            return None
        oid = id(blob)
        if oid in seen:
            return None
        seen.add(oid)
        if "characters" in blob or "input_tokens" in blob or "output_tokens" in blob:
            return blob
        for key in ("usage", "payload", "raw_task", "raw", "result"):
            if key not in blob:
                continue
            found = walk(blob[key])
            if found is not None:
                return found
        return None

    return walk(payload)


def settle_qwen_tts_points(model: str, usage: dict[str, object], *, base_url: str | None = None) -> int | None:
    """没有用量就返回 None,维持预扣。3.1 只认 token,不拿字符数冒充。"""
    blob = _find_usage(usage)
    if blob is None:
        return None
    region = qwen_tts_region(_resolved_base(base_url))
    if model == QWEN_TTS_31:
        if "input_tokens" not in blob and "output_tokens" not in blob:
            return None
        incoming = _nonneg_int(blob["input_tokens"]) if "input_tokens" in blob else 0
        outgoing = _nonneg_int(blob["output_tokens"]) if "output_tokens" in blob else 0
        if incoming is None or outgoing is None:
            return None
        if incoming == 0 and outgoing == 0:
            return None
        return _token_points(incoming, outgoing, region)
    if model not in (QWEN_TTS_FLASH, QWEN_TTS_PLUS):
        return None
    if "characters" not in blob:
        return None
    chars = _nonneg_int(blob["characters"])
    if chars is None:
        return None
    micro = _CHAR_MICRO_PER_10K.get((model, region))
    if micro is None:
        return None
    return _char_points(chars, micro)


__all__ = [
    "QWEN_INSTRUCTION_LIMIT",
    "estimate_qwen_tts_points",
    "qwen_tts_region",
    "qwen_weighted_chars",
    "settle_qwen_tts_points",
]
