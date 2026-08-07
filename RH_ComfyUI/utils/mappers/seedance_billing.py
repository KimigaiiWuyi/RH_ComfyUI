"""Seedance 系列视频生成动态积分计价

计价规则(取自官方文档):
  token 用量估算公式:
    (输入视频时长 + 输出视频时长) × 输出视频宽 × 输出视频高 × 输出视频帧率 / 1024

  其中「输入视频时长」为所有参考视频时长之和(秒);无参考视频时为 0。

  doubao-seedance-2.0:
    480p/720p: 无输入视频 46 元/M tokens,有输入视频 28 元/M tokens
    1080p:     无输入视频 51 元/M tokens,有输入视频 31 元/M tokens
    4K:        无输入视频 26 元/M tokens,有输入视频 16 元/M tokens

  doubao-seedance-2.0-fast (仅 480p/720p):
    无输入视频 37 元/M tokens,有输入视频 22 元/M tokens

  doubao-seedance-2.0-mini (仅 480p/720p):
    无输入视频 23 元/M tokens,有输入视频 14 元/M tokens

  doubao-seedance-1.5-pro:
    有声视频 16 元/M tokens,无声视频 8 元/M tokens

  doubao-seedance-1.0-pro:
    15 元/M tokens

  doubao-seedance-2.5 (仅 480p/720p,时长 4~30s 或 -1):
    无输入视频 70 元/M tokens,有输入视频 42 元/M tokens
    例:720p 16:9 5s 无输入 → 108000 tokens × 70 元/M = 7.56 元 = 756 积分
    duration=-1(跟随输入)时:输出时长优先取输入视频总时长,否则 15s。

point_cost 仅作兜底。
"""

from __future__ import annotations

from typing import Any, Optional

# ── 计费常量 ──

# 元 → 积分换算:1 元 = 100 积分
_YUAN_TO_POINTS: int = 100

# ── 分辨率 → 像素尺寸 + 帧率 ──

_RESOLUTION_SPECS: dict[str, tuple[int, int, int]] = {
    # resolution: (width, height, fps)
    "480p": (854, 480, 24),
    "720p": (1280, 720, 24),
    "1080p": (1920, 1080, 24),
    "4k": (3840, 2160, 24),
    "4K": (3840, 2160, 24),
}

# ── Seedance 2.0 费率(元/M tokens) ──

_SEEDANCE2_RATES = {
    # resolution: (无输入视频费率, 有输入视频费率)
    "480p": (46.00, 28.00),
    "720p": (46.00, 28.00),
    "1080p": (51.00, 31.00),
    "4k": (26.00, 16.00),
    "4K": (26.00, 16.00),
}

# ── Seedance 2.0 Fast 费率(元/M tokens,仅 480p/720p) ──

_SEEDANCE2_FAST_RATES = {
    "480p": (37.00, 22.00),
    "720p": (37.00, 22.00),
}

# ── Seedance 2.0 Mini 费率(元/M tokens,仅 480p/720p) ──

_SEEDANCE2_MINI_RATES = {
    "480p": (23.00, 14.00),
    "720p": (23.00, 14.00),
}

# ── Seedance 2.5 费率(元/M tokens,仅 480p/720p) ──

_SEEDANCE25_RATES = {
    # resolution: (无输入视频费率, 有输入视频费率)
    "480p": (70.00, 42.00),
    "720p": (70.00, 42.00),
}

# duration=-1 且无法解析输入时长时的代理输出秒数
_SEEDANCE25_AUTO_DURATION: float = 15.0

# ── Seedance 1.5 Pro 费率(元/M tokens) ──

_SEEDANCE15_PRO_WITH_AUDIO: float = 16.00
_SEEDANCE15_PRO_WITHOUT_AUDIO: float = 8.00

# ── Seedance 1.0 Pro 费率(元/M tokens) ──

_SEEDANCE10_PRO_RATE: float = 15.00

# ── 单段参考视频时长未知时的默认秒数(× 段数) ──

_DEFAULT_INPUT_VIDEO_DURATION: float = 5.0


def _calculate_tokens(
    input_duration: float,
    output_duration: float,
    width: int,
    height: int,
    fps: int,
) -> float:
    """计算 token 用量。

    公式:(输入视频时长 + 输出视频时长) × 输出视频宽 × 输出视频高 × 输出视频帧率 / 1024
    """
    return (max(input_duration, 0.0) + max(output_duration, 0.0)) * width * height * fps / 1024


def _tokens_to_points(tokens: float, rate_yuan_per_million: float) -> int:
    """将 token 数转换为积分(向上取整,最小 1 积分)。

    rate_yuan_per_million:每百万 token 的费率(元)
    """
    points_per_million = rate_yuan_per_million * _YUAN_TO_POINTS
    points = tokens * points_per_million / 1_000_000
    return max(int(points) + (1 if points > int(points) else 0), 1)


def _get_resolution_spec(resolution: str) -> tuple[int, int, int]:
    """获取分辨率对应的(宽, 高, 帧率)。

    未匹配时默认 720p。
    """
    return _RESOLUTION_SPECS.get(resolution, _RESOLUTION_SPECS["720p"])


def _has_input_video(video_refs: Optional[list]) -> bool:
    """判断是否包含输入视频。"""
    return bool(video_refs) and len(video_refs) > 0


def _clip_duration_seconds(ref: Any) -> Optional[float]:
    """从单条参考媒体尽量读出时长(秒);读不到返回 None。

    支持:
    - 数值本身(测试/占位可直接塞 float 秒)
    - MediaRef.duration / .duration_seconds
    - dict["duration"] / dict["duration_seconds"]
    - 对象 metadata 上的 duration 字段
    """
    if ref is None:
        return None
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        val = float(ref)
        return val if val > 0 else None
    if isinstance(ref, dict):
        for key in ("duration", "duration_seconds", "duration_s"):
            raw = ref.get(key)
            if raw is not None:
                try:
                    val = float(raw)
                    if val > 0:
                        return val
                except (TypeError, ValueError):
                    pass
        return None
    for key in ("duration", "duration_seconds", "duration_s"):
        raw = getattr(ref, key, None)
        if raw is not None:
            try:
                val = float(raw)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                pass
    meta = getattr(ref, "metadata", None)
    if isinstance(meta, dict):
        for key in ("duration", "duration_seconds", "duration_s"):
            raw = meta.get(key)
            if raw is not None:
                try:
                    val = float(raw)
                    if val > 0:
                        return val
                except (TypeError, ValueError):
                    pass
    return None


def input_video_duration_from_params(params: Optional[dict[str, Any]]) -> Optional[float]:
    """从 request.params 读显式输入视频总时长(秒)。"""
    if not params:
        return None
    raw = params.get("input_video_duration")
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if val >= 0 else None


def resolve_input_video_duration(
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> float:
    """解析「输入视频总时长」(秒),供 token 公式使用。

    优先级:
    1. 显式 ``input_video_duration``(estimate API / request.params,前端可传总秒数)
    2. 累加 ``video_refs`` 每段可解析时长
    3. 有参考视频但全无时长: ``默认 5s × 段数``
    4. 无参考视频: ``0``
    """
    if input_video_duration is not None:
        try:
            val = float(input_video_duration)
            if val >= 0:
                return val
        except (TypeError, ValueError):
            pass

    refs = list(video_refs or [])
    if not refs:
        return 0.0

    known = 0.0
    unknown = 0
    for ref in refs:
        d = _clip_duration_seconds(ref)
        if d is not None:
            known += d
        else:
            unknown += 1

    if unknown == 0:
        return known
    # 部分已知 + 部分未知:已知累加 + 未知用默认秒
    return known + unknown * _DEFAULT_INPUT_VIDEO_DURATION


def estimate_seedance2_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """Seedance 2.0 估算积分。

    Args:
        resolution: 输出分辨率(480p/720p/1080p/4k)
        output_duration: 输出视频时长(秒)
        video_refs: 输入视频参考列表(用于有/无输入档 + 累加时长)
        input_video_duration: 显式输入视频总时长(秒);优先于从 video_refs 推断
    """
    width, height, fps = _get_resolution_spec(resolution)
    in_dur = resolve_input_video_duration(video_refs, input_video_duration=input_video_duration)
    has_input = in_dur > 0 or _has_input_video(video_refs)

    tokens = _calculate_tokens(in_dur, float(output_duration), width, height, fps)
    rate = _SEEDANCE2_RATES.get(resolution, _SEEDANCE2_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance2_fast_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """Seedance 2.0 Fast 估算积分(仅支持 480p/720p)。"""
    width, height, fps = _get_resolution_spec(resolution)
    in_dur = resolve_input_video_duration(video_refs, input_video_duration=input_video_duration)
    has_input = in_dur > 0 or _has_input_video(video_refs)

    tokens = _calculate_tokens(in_dur, float(output_duration), width, height, fps)
    rate = _SEEDANCE2_FAST_RATES.get(resolution, _SEEDANCE2_FAST_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance2_mini_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """Seedance 2.0 Mini 估算积分(仅支持 480p/720p)。"""
    width, height, fps = _get_resolution_spec(resolution)
    in_dur = resolve_input_video_duration(video_refs, input_video_duration=input_video_duration)
    has_input = in_dur > 0 or _has_input_video(video_refs)

    tokens = _calculate_tokens(in_dur, float(output_duration), width, height, fps)
    rate = _SEEDANCE2_MINI_RATES.get(resolution, _SEEDANCE2_MINI_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance25_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """Seedance 2.5 估算积分(仅 480p/720p;时长 4~30 或 -1)。

    duration=-1 时:输出时长 = 输入视频总时长(若 >0),否则 15s。
    """
    in_dur = resolve_input_video_duration(video_refs, input_video_duration=input_video_duration)
    has_input = in_dur > 0 or _has_input_video(video_refs)

    dur = float(output_duration)
    if dur < 0:
        dur = in_dur if in_dur > 0 else _SEEDANCE25_AUTO_DURATION

    width, height, fps = _get_resolution_spec(resolution)
    tokens = _calculate_tokens(in_dur, dur, width, height, fps)
    rate = _SEEDANCE25_RATES.get(resolution, _SEEDANCE25_RATES["720p"])
    rate_yuan = rate[1] if has_input else rate[0]

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance15_pro_points(
    resolution: str,
    output_duration: float,
    generate_audio: bool = True,
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """Seedance 1.5 Pro 估算积分。

    有声视频 16 元/M,无声视频 8 元/M。
    """
    width, height, fps = _get_resolution_spec(resolution)
    in_dur = resolve_input_video_duration(video_refs, input_video_duration=input_video_duration)

    tokens = _calculate_tokens(in_dur, float(output_duration), width, height, fps)
    rate_yuan = _SEEDANCE15_PRO_WITH_AUDIO if generate_audio else _SEEDANCE15_PRO_WITHOUT_AUDIO

    return _tokens_to_points(tokens, rate_yuan)


def estimate_seedance10_pro_points(
    resolution: str,
    output_duration: float,
    video_refs: Optional[list] = None,
    *,
    input_video_duration: Optional[float] = None,
) -> int:
    """Seedance 1.0 Pro 估算积分(固定 15 元/M tokens)。"""
    width, height, fps = _get_resolution_spec(resolution)
    in_dur = resolve_input_video_duration(video_refs, input_video_duration=input_video_duration)

    tokens = _calculate_tokens(in_dur, float(output_duration), width, height, fps)
    return _tokens_to_points(tokens, _SEEDANCE10_PRO_RATE)


__all__ = [
    "estimate_seedance2_points",
    "estimate_seedance2_fast_points",
    "estimate_seedance2_mini_points",
    "estimate_seedance25_points",
    "estimate_seedance15_pro_points",
    "estimate_seedance10_pro_points",
    "resolve_input_video_duration",
    "input_video_duration_from_params",
    "_calculate_tokens",
    "_tokens_to_points",
    "_get_resolution_spec",
    "_DEFAULT_INPUT_VIDEO_DURATION",
    "_SEEDANCE2_RATES",
    "_SEEDANCE2_FAST_RATES",
    "_SEEDANCE2_MINI_RATES",
    "_SEEDANCE25_RATES",
    "_SEEDANCE25_AUTO_DURATION",
    "_SEEDANCE15_PRO_WITH_AUDIO",
    "_SEEDANCE15_PRO_WITHOUT_AUDIO",
    "_SEEDANCE10_PRO_RATE",
]
