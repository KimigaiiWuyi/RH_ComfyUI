"""GPT-Image-2 动态积分计价 + ratio/size 像素真源

计价规则:210 元(RMB)/1M tokens,1 积分 = 1 分钱 → 210 元 = 21_000 积分 / 1M tokens。
token 计算公式由上游 OpenAI 兼容网关公开,按 quality 档位 + 输出像素面积分档。

## `_RATIO_SIZE_MAP` 是唯一真源

本表同时驱动:
  1. 积分估算 (`resolve_dimensions` / `estimate_gpt_image2_points`)
  2. 实际上游 `size` 参数 (`resolve_size_string` → GPTImage2API / openai_image)
  3. 模型目录 ratio 枚举 (`ratio_enum_values` → GptImage2Def / BananaProDef)

禁止在 backends 或 defs 再维护第二份 ratio→像素表。
"""

from __future__ import annotations

from typing import Mapping, Optional

# ── 模型 spec(取自网关公开文档) ──

# 2.0 / 2.5 同 $/1M token(image out $30 → POINTS_PER_MILLION_TOKENS)。
# 2.5 high 对齐 2.0 medium;2.5 max 对齐 2.0 high;low 对齐 2.0;medium/xhigh 插值。
_GPT_IMAGE2_QUALITY_AXIS: dict[str, int] = {"low": 16, "medium": 48, "high": 96}
_GPT_IMAGE25_QUALITY_AXIS: dict[str, int] = {
    "low": 16,
    "medium": 32,
    "high": 48,
    "xhigh": 72,
    "max": 96,
}


def _gpt_image_family_spec(quality_axis_factors: dict[str, int]) -> dict:
    return {
        "size_limits": {
            "step_px": 16,
            "min_pixels": 655_360,
            "max_pixels": 8_294_400,
            "max_dimension_px": 3_840,
            "max_aspect_ratio": 3.0,
        },
        "quality_axis_factors": dict(quality_axis_factors),
        "token_area_offset_pixels": 2_000_000,
        "token_area_scale_denominator": 4_000_000,
    }


IMAGE_MODEL_SPECS: dict[str, dict] = {
    "gpt-image-2": _gpt_image_family_spec(_GPT_IMAGE2_QUALITY_AXIS),
    "gpt-image-2.5-sunburst": _gpt_image_family_spec(_GPT_IMAGE25_QUALITY_AXIS),
    "gpt-image-2.5-flare": _gpt_image_family_spec(_GPT_IMAGE25_QUALITY_AXIS),
}

# ── 计费常量 ──

# 210 元 / 1M tokens,1 元 = 100 积分 → 210 * 100 = 21_000 积分 / 1M tokens
# 预扣按输出 token 估;实扣按官方 USD/1M × 7 元 × 100 分项(2.0/2.5 同价)。
POINTS_PER_MILLION_TOKENS: int = 21_000
POINTS_PER_MILLION_IMAGE_OUTPUT: int = POINTS_PER_MILLION_TOKENS  # $30
POINTS_PER_MILLION_IMAGE_INPUT: int = 5_600  # $8
POINTS_PER_MILLION_IMAGE_CACHED: int = 1_400  # $2
POINTS_PER_MILLION_TEXT_INPUT: int = 3_500  # $5
POINTS_PER_MILLION_TEXT_CACHED: int = 875  # $1.25

# ratio + image_size → 像素尺寸(计费 / 实际上游 size / schema 共用)
#
# 每个 cell 都必须满足 OpenAI 上游的 4 条硬约束(参见 IMAGE_MODEL_SPECS.size_limits):
#   1. 两边都是 16 的倍数
#   2. max edge ≤ 3840px
#   3. 长宽比 = ratio(精确),且 ≤ 3:1
#   4. 像素 ∈ [655_360, 8_294_400]
# 此外:总像素 > 3_686_400 (即 2560x1440) 视为 2K+ 实验性输出。
# 本表以 16:9 2K = 2560x1440 为基准,其余 2K 行同比例上抬;4K 行贴近硬上限,
# tier 语义:短边 ≈ 1K/2K/4K。
_RATIO_SIZE_MAP: dict[str, dict[str, str]] = {
    # Landscape (width > height)
    "1:1": {"1K": "1024x1024", "2K": "2560x2560", "4K": "2880x2880"},
    "16:9": {"1K": "1792x1008", "2K": "2560x1440", "4K": "3840x2160"},
    "8:5": {"1K": "1280x800", "2K": "2560x1600", "4K": "3200x2000"},
    "4:3": {"1K": "1280x960", "2K": "2560x1920", "4K": "3264x2448"},
    "3:2": {"1K": "1536x1024", "2K": "3072x2048", "4K": "3504x2336"},
    "2:1": {"1K": "1152x576", "2K": "2560x1280", "4K": "3840x1920"},
    "21:9": {"1K": "2464x1056", "2K": "2800x1200", "4K": "3808x1632"},
    # Portrait (width < height)
    "9:16": {"1K": "1008x1792", "2K": "1440x2560", "4K": "2160x3840"},
    "3:4": {"1K": "960x1280", "2K": "1920x2560", "4K": "2448x3264"},
    "2:3": {"1K": "1024x1536", "2K": "2048x3072", "4K": "2336x3504"},
    "1:2": {"1K": "576x1152", "2K": "1280x2560", "4K": "1920x3840"},
}

# 前端 /models schema 展示顺序(auto 置顶;表中新增 ratio 未列入时会追加到末尾)
_RATIO_ENUM_ORDER: tuple[str, ...] = (
    "auto",
    "1:1",
    "16:9",
    "8:5",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "21:9",
)

# ratio="auto" 时的默认估算尺寸
_AUTO_WIDTH: int = 1024
_AUTO_HEIGHT: int = 1024
_FALLBACK_SIZE_STR: str = "2048x2048"


def _parse_size(size_str: str) -> tuple[int, int]:
    """'1024x1024' → (1024, 1024)"""
    w, _, h = size_str.lower().partition("x")
    return int(w), int(h)


def ratio_enum_values() -> list[str]:
    """模型目录 ratio 枚举:auto + 表中全部 ratio(顺序稳定)。"""
    known = set(_RATIO_SIZE_MAP.keys())
    out = [r for r in _RATIO_ENUM_ORDER if r == "auto" or r in known]
    for key in _RATIO_SIZE_MAP:
        if key not in out:
            out.append(key)
    return out


def resolve_size_string(ratio: Optional[str], image_size: Optional[str]) -> str:
    """ratio + image_size → 上游 size 字符串(如 '2560x1440' 或 'auto')。

    实际生图(GPTImage2API / openai_image.size_for)必须走这里,与计费同源。
    ratio 为 'auto' / None / 未匹配 → 'auto'(交给上游自适应)。
    已知 ratio 但 tier 缺失 → 回落 2K;2K 也缺失 → `_FALLBACK_SIZE_STR`。
    """
    if not ratio or ratio == "auto":
        return "auto"
    tier = image_size if image_size in ("1K", "2K", "4K") else "2K"
    tier_map = _RATIO_SIZE_MAP.get(ratio)
    if tier_map is None:
        return "auto"
    return tier_map.get(tier) or tier_map.get("2K") or _FALLBACK_SIZE_STR


def resolve_dimensions(ratio: Optional[str], image_size: Optional[str]) -> tuple[int, int]:
    """ratio + image_size → (width, height) 像素。

    ratio 为 'auto' / None / 未匹配 → 回落 _AUTO_WIDTH x _AUTO_HEIGHT。
    与 resolve_size_string 同源(auto 时估价用默认正方形,上游 size 用 'auto')。
    """
    if not ratio or ratio == "auto":
        return _AUTO_WIDTH, _AUTO_HEIGHT
    size_str = resolve_size_string(ratio, image_size)
    if size_str == "auto":
        return _AUTO_WIDTH, _AUTO_HEIGHT
    return _parse_size(size_str)


def calculate_image_tokens(
    quality: str,
    width: int,
    height: int,
    model: str = "gpt-image-2",
) -> int:
    """计算生成一张图消耗的 tokens(纯函数,无 IO)。

    公式(上游公开):
        long_edge  = max(width, height)
        short_edge = min(width, height)
        short_axis_factor =
            (2 * quality_axis_factor * short_edge + long_edge) // (2 * long_edge)
        tokens =
            (quality_axis_factor
             * short_axis_factor
             * (token_area_offset_pixels + width * height)
             + token_area_scale_denominator - 1)
            // token_area_scale_denominator
    """
    resolved = model if model in IMAGE_MODEL_SPECS else "gpt-image-2"
    spec = IMAGE_MODEL_SPECS[resolved]
    quality_axis_factor = spec["quality_axis_factors"][quality]
    long_edge = max(width, height)
    short_edge = min(width, height)
    short_axis_factor = (2 * quality_axis_factor * short_edge + long_edge) // (2 * long_edge)

    return (
        quality_axis_factor * short_axis_factor * (spec["token_area_offset_pixels"] + width * height)
        + spec["token_area_scale_denominator"]
        - 1
    ) // spec["token_area_scale_denominator"]


def calculate_image_points(
    quality: str,
    width: int,
    height: int,
    model: str = "gpt-image-2",
) -> int:
    """计算生成一张图消耗的积分(分),向上取整到积分。

    = tokens / 1_000_000 * POINTS_PER_MILLION_TOKENS,最小 1 积分。
    """
    tokens = calculate_image_tokens(quality, width, height, model)
    # tokens * 21_000 / 1_000_000 = tokens * 21 / 1000 (积分)
    points = (tokens * POINTS_PER_MILLION_TOKENS + 999_999) // 1_000_000
    return max(points, 1)


def estimate_gpt_image2_points(
    quality: Optional[str],
    ratio: Optional[str],
    image_size: Optional[str],
    model: str = "gpt-image-2",
) -> int:
    """从请求参数直接估算积分(供 estimate_cost 调用)。

    quality 不在该 model 的档位表 → medium;ratio 缺失 / auto → 1024x1024。
    model 未登记 → gpt-image-2。2.5 与 2.0 同单价,轴因子不同。
    """
    resolved = model if model in IMAGE_MODEL_SPECS else "gpt-image-2"
    factors = IMAGE_MODEL_SPECS[resolved]["quality_axis_factors"]
    q = quality if isinstance(quality, str) and quality in factors else "medium"
    w, h = resolve_dimensions(ratio, image_size)
    return calculate_image_points(q, w, h, resolved)


_TOKEN_BLOB_KEYS: tuple[str, ...] = ("input_tokens", "output_tokens", "total_tokens")
# result: AI 基座 task/info 把上游 JSON 放在 data.result
_USAGE_NEST_KEYS: tuple[str, ...] = (
    "rawUsage",
    "raw_usage",
    "usage",
    "raw_task",
    "raw",
    "result",
)


def _nonneg_int(val: object) -> int:
    if val is None or isinstance(val, bool):
        return 0
    try:
        n = int(val)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _mapping_int(blob: Mapping[str, object], key: str) -> int:
    if key not in blob:
        return 0
    return _nonneg_int(blob[key])


def _child_mapping(blob: Mapping[str, object], key: str) -> Mapping[str, object]:
    if key not in blob:
        return {}
    val = blob[key]
    return val if isinstance(val, Mapping) else {}


def _looks_like_token_blob(blob: Mapping[str, object]) -> bool:
    if any(key in blob for key in _TOKEN_BLOB_KEYS):
        return True
    return "input_tokens_details" in blob or "output_tokens_details" in blob


def _blob_score(blob: Mapping[str, object]) -> int:
    score = 0
    if "input_tokens_details" in blob:
        score += 8
    if "output_tokens" in blob:
        score += 4
    if "input_tokens" in blob:
        score += 4
    if "output_tokens_details" in blob:
        score += 2
    if "total_tokens" in blob:
        score += 1
    return score


def extract_gpt_image_token_blob(usage: object) -> dict[str, object] | None:
    """从官方 usage / 网关 rawUsage / dispatcher raw_task 取出 token 对象。"""
    if not isinstance(usage, dict):
        return None
    found: list[dict[str, object]] = []
    stack: list[object] = [usage]
    seen: set[int] = set()
    while stack:
        cur = stack.pop()
        if not isinstance(cur, dict):
            continue
        oid = id(cur)
        if oid in seen:
            continue
        seen.add(oid)
        if _looks_like_token_blob(cur):
            found.append(cur)
        for key in _USAGE_NEST_KEYS:
            if key in cur:
                stack.append(cur[key])
    if not found:
        return None
    return max(found, key=_blob_score)


def split_gpt_image_tokens(blob: Mapping[str, object]) -> dict[str, int] | None:
    """拆成 image/text × input/cached/output。无法识别时 None。"""
    in_details = _child_mapping(blob, "input_tokens_details")
    out_details = _child_mapping(blob, "output_tokens_details")
    output_total = _mapping_int(blob, "output_tokens")
    input_total = _mapping_int(blob, "input_tokens")
    cached_total = _mapping_int(blob, "cached_tokens")
    if cached_total == 0:
        cached_total = _mapping_int(in_details, "cached_tokens")

    image_out = _mapping_int(out_details, "image_tokens")
    if image_out == 0 and output_total > 0:
        image_out = output_total

    image_in = _mapping_int(in_details, "image_tokens")
    text_in = _mapping_int(in_details, "text_tokens")
    if image_in == 0 and text_in == 0:
        image_in = input_total

    image_cached = min(cached_total, image_in)
    text_cached = min(max(cached_total - image_cached, 0), text_in)
    image_uncached = max(image_in - image_cached, 0)
    text_uncached = max(text_in - text_cached, 0)
    if image_out + image_uncached + image_cached + text_uncached + text_cached <= 0:
        return None
    return {
        "image_output": image_out,
        "image_input": image_uncached,
        "image_cached": image_cached,
        "text_input": text_uncached,
        "text_cached": text_cached,
    }


def _points_from_token_rates(parts: Mapping[str, int]) -> int:
    weighted = (
        parts["image_output"] * POINTS_PER_MILLION_IMAGE_OUTPUT
        + parts["image_input"] * POINTS_PER_MILLION_IMAGE_INPUT
        + parts["image_cached"] * POINTS_PER_MILLION_IMAGE_CACHED
        + parts["text_input"] * POINTS_PER_MILLION_TEXT_INPUT
        + parts["text_cached"] * POINTS_PER_MILLION_TEXT_CACHED
    )
    if weighted <= 0:
        return 0
    return max((weighted + 999_999) // 1_000_000, 1)


def settle_gpt_image2_points(usage: object) -> Optional[int]:
    """供应商 usage → 实扣积分;无法解析时 None(维持预扣)。"""
    blob = extract_gpt_image_token_blob(usage)
    if blob is None:
        return None
    parts = split_gpt_image_tokens(blob)
    if parts is None:
        return None
    points = _points_from_token_rates(parts)
    return points if points > 0 else None


def normalize_gpt_image_usage(payload: object) -> dict[str, object]:
    """通道 NodeOutput.usage:带 raw_usage 供 settle;无 token 则空 dict。"""
    blob = extract_gpt_image_token_blob(payload)
    if blob is None:
        return {}
    return {
        "vendor_unit": "tokens",
        "raw_usage": blob,
        "input_tokens": _mapping_int(blob, "input_tokens"),
        "output_tokens": _mapping_int(blob, "output_tokens"),
        "total_tokens": _mapping_int(blob, "total_tokens"),
    }


def sanitize_gpt_image_raw(payload: object) -> dict[str, object]:
    """落 raw 时丢掉 b64_json,避免统计表被整图撑爆。"""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, object] = dict(payload)
    if "data" not in out or not isinstance(out["data"], list):
        return out
    cleaned: list[object] = []
    for item in out["data"]:
        if isinstance(item, dict) and "b64_json" in item:
            slim = dict(item)
            slim["b64_json"] = "<omitted>"
            cleaned.append(slim)
        else:
            cleaned.append(item)
    out["data"] = cleaned
    return out


__all__ = [
    "IMAGE_MODEL_SPECS",
    "POINTS_PER_MILLION_TOKENS",
    "POINTS_PER_MILLION_IMAGE_OUTPUT",
    "POINTS_PER_MILLION_IMAGE_INPUT",
    "POINTS_PER_MILLION_IMAGE_CACHED",
    "POINTS_PER_MILLION_TEXT_INPUT",
    "POINTS_PER_MILLION_TEXT_CACHED",
    "_RATIO_SIZE_MAP",
    "ratio_enum_values",
    "resolve_size_string",
    "resolve_dimensions",
    "calculate_image_tokens",
    "calculate_image_points",
    "estimate_gpt_image2_points",
    "extract_gpt_image_token_blob",
    "settle_gpt_image2_points",
    "normalize_gpt_image_usage",
    "sanitize_gpt_image_raw",
]
