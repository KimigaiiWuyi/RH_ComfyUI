"""gemini_omni_billing — Gemini Omni 视频按输出 token 计费

官方刊例(USD / 1M tokens):
- 视频输出:1.2 $12;1.1 $17.5(2026-12-03 起上游自动路由 1.2 并按 1.2 计费)
- 输入(文/图/视频统一):$1.5

视频输出 token 率(tokens/秒,官方给值):
- 360p 1931 / 720p 5792(默认)/ 1080p 8688 / 4k 17376

换算沿用 gpt_image2 惯例:USD × 7 元 × 100 分/元。
即 720p 每秒 ≈ 5792 × 12 × 700 / 1e6 ≈ 48.65 积分。

预扣按请求分辨率+时长×输出 token 率估算;输入 token(提示词+参考媒体)
按保守定值估算并入预扣。成功后若上游返回 token 用量,以实际输出 token
结算;未返回则维持预扣。
"""

from __future__ import annotations

from typing import Any, Optional

_YUAN_PER_USD = 7.0
_YUAN_TO_POINTS = 100
_USD_PER_MTOK_VIDEO_OUT = 12.0
_USD_PER_MTOK_IN = 1.5

# 官方:1 秒视频的输出 token 数(按分辨率)
_VIDEO_TOKENS_PER_SECOND: dict[str, int] = {
    "360p": 1931,
    "720p": 5792,
    "1080p": 8688,
    "4k": 17376,
}

# 输入侧保守估算(图片经预处理 ≤2048px,约 5~6 tile;参考视频按输出 token 率计)
_INPUT_TOKENS_PER_IMAGE = 1600
_INPUT_TOKENS_TEXT = 400


def _usd_to_points(usd: float) -> int:
    return int(round(usd * _YUAN_PER_USD * _YUAN_TO_POINTS))


def _out_tokens(resolution: str, duration_s: float) -> float:
    rate = _VIDEO_TOKENS_PER_SECOND.get(resolution.lower(), _VIDEO_TOKENS_PER_SECOND["720p"])
    return rate * max(0.0, duration_s)


def _in_tokens(n_images: int, video_refs_duration_s: float, resolution: str) -> float:
    rate = _VIDEO_TOKENS_PER_SECOND.get(resolution.lower(), _VIDEO_TOKENS_PER_SECOND["720p"])
    return _INPUT_TOKENS_TEXT + _INPUT_TOKENS_PER_IMAGE * n_images + rate * max(0.0, video_refs_duration_s)


def _price_points(out_tokens: float, in_tokens: float) -> int:
    usd = (out_tokens * _USD_PER_MTOK_VIDEO_OUT + in_tokens * _USD_PER_MTOK_IN) / 1_000_000
    return max(1, _usd_to_points(usd))


def estimate_gemini_omni_points(
    resolution: str = "720p",
    duration_s: float = 10,
    *,
    n_images: int = 0,
    video_refs_duration_s: float = 0.0,
) -> int:
    """预扣:输出按 秒×分辨率 token 率 ×$12/M;输入按定值估算 ×$1.5/M。"""
    return _price_points(
        _out_tokens(resolution, duration_s),
        _in_tokens(n_images, video_refs_duration_s, resolution),
    )


def settle_gemini_omni_points(
    usage: dict[str, Any],
    resolution: str,
    *,
    n_images: int = 0,
    video_refs_duration_s: float = 0.0,
) -> Optional[int]:
    """实扣:上游回了输出 token 就按实结算;没有 token 用量返回 None 维持预扣。"""
    out_tokens: Optional[float] = None
    for key in ("video_output_tokens", "output_tokens", "total_output_tokens"):
        val = usage.get(key)
        if val is not None:
            try:
                out_tokens = float(val)
                break
            except (TypeError, ValueError):
                continue
    if out_tokens is None:
        return None
    in_tokens = _in_tokens(n_images, video_refs_duration_s, resolution)
    prompt_tokens = usage.get("prompt_tokens") or usage.get("total_input_tokens")
    if prompt_tokens is not None:
        try:
            in_tokens = max(in_tokens, float(prompt_tokens))
        except (TypeError, ValueError):
            pass
    return _price_points(out_tokens, in_tokens)


__all__ = ["estimate_gemini_omni_points", "settle_gemini_omni_points"]
