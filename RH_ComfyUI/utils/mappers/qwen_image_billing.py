"""千问图像积分。

1 元 = 100 积分,向上取整,至少 1。
北京价(其它非新加坡主机也用这档):
  3.0 Pro 输入 0.02,输出 1K 0.25 / 2K 0.50
  3.0 输入 0.02,输出 1K 与 2K 都是 0.18
  2.1 Pro 0.25/张,2.1 Turbo 0.10/张,输入不计
  2.0 Pro 0.50/张,2.0 0.20/张,输入不计
新加坡(主机名含 dashscope-intl 或 ap-southeast)用国际标价。
面积 ≤ 2,250,000 为 1K。ratio 为 auto 或未传时不发 size,
3.0 Pro 预扣按 2K,成功后按 usage 里的宽高结算。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .qwen_image_catalog import (
    QWEN_IMAGE_20,
    QWEN_IMAGE_30,
    TIER_1K_MAX_AREA,
    QWEN_IMAGE_20_PRO,
    QWEN_IMAGE_21_PRO,
    QWEN_IMAGE_30_PRO,
    QWEN_IMAGE_21_TURBO,
)
from ..backends.dashscope.config import DASHSCOPE_DEFAULT_BASE_URL, dashscope_credentials

# 微元:1 元 = 1_000_000。积分 = ceil(微元 * 张数 / 10_000)。
_MICRO = 1_000_000


@dataclass(frozen=True)
class _Rate:
    output_1k: int
    output_2k: int
    input_image: int


def _yuan(text: str) -> int:
    whole, frac = text.split(".")
    return int(whole) * _MICRO + int(frac.ljust(6, "0"))


_CN: dict[str, _Rate] = {
    QWEN_IMAGE_30_PRO: _Rate(_yuan("0.25"), _yuan("0.50"), _yuan("0.02")),
    QWEN_IMAGE_30: _Rate(_yuan("0.18"), _yuan("0.18"), _yuan("0.02")),
    QWEN_IMAGE_21_PRO: _Rate(_yuan("0.25"), _yuan("0.25"), 0),
    QWEN_IMAGE_21_TURBO: _Rate(_yuan("0.10"), _yuan("0.10"), 0),
    QWEN_IMAGE_20_PRO: _Rate(_yuan("0.50"), _yuan("0.50"), 0),
    QWEN_IMAGE_20: _Rate(_yuan("0.20"), _yuan("0.20"), 0),
}
_INTL: dict[str, _Rate] = {
    QWEN_IMAGE_30_PRO: _Rate(_yuan("0.299768"), _yuan("0.562065"), _yuan("0.022483")),
    QWEN_IMAGE_30: _Rate(_yuan("0.224826"), _yuan("0.224826"), _yuan("0.022483")),
    QWEN_IMAGE_21_PRO: _Rate(_yuan("0.283404"), _yuan("0.283404"), 0),
    QWEN_IMAGE_21_TURBO: _Rate(_yuan("0.113362"), _yuan("0.113362"), 0),
    QWEN_IMAGE_20_PRO: _Rate(_yuan("0.550443"), _yuan("0.550443"), 0),
    QWEN_IMAGE_20: _Rate(_yuan("0.256873"), _yuan("0.256873"), 0),
}


def qwen_image_region(base_url: str) -> str:
    host = urlsplit(base_url).netloc.lower()
    if "dashscope-intl" in host or "ap-southeast" in host:
        return "intl"
    return "cn"


def _resolved_base(base_url: str | None) -> str:
    if base_url is not None and base_url.strip():
        return base_url.strip()
    creds = dashscope_credentials()
    return creds.base_url or DASHSCOPE_DEFAULT_BASE_URL


def _points(micro: int, count: int) -> int:
    if micro <= 0 or count <= 0:
        return 0
    return (micro * count + 9_999) // 10_000


def _rate(model: str, region: str) -> _Rate | None:
    table = _INTL if region == "intl" else _CN
    if model in table:
        return table[model]
    return None


def prepaid_tier(model: str, image_size: str | None, ratio: str | None) -> str:
    """省略 size 时 3.0 Pro 可能出到 2K,预扣走高档。"""
    omitted = ratio is None or ratio == "" or ratio == "auto"
    if omitted and model == QWEN_IMAGE_30_PRO:
        return "2k"
    if isinstance(image_size, str) and image_size.strip().upper() == "2K":
        return "2k"
    return "1k"


def calculate_qwen_image_points(
    model: str,
    *,
    tier: str,
    num_input_images: int,
    output_count: int,
    region: str,
) -> int:
    rate = _rate(model, region)
    if rate is None or output_count <= 0:
        return 1
    output_micro = rate.output_2k if tier == "2k" else rate.output_1k
    total = _points(rate.input_image, num_input_images) + _points(output_micro, output_count)
    return max(1, total)


def estimate_qwen_image_points(
    model: str,
    *,
    image_size: str | None = None,
    ratio: str | None = None,
    num_input_images: int = 0,
    n: int = 1,
    base_url: str | None = None,
) -> int:
    count = n if n > 0 else 1
    region = qwen_image_region(_resolved_base(base_url))
    tier = prepaid_tier(model, image_size, ratio)
    return calculate_qwen_image_points(
        model,
        tier=tier,
        num_input_images=max(0, num_input_images),
        output_count=count,
        region=region,
    )


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, object] = {}
    for key, item in value.items():
        if isinstance(key, str):
            out[key] = item
    return out


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _merged_usage(usage: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {}
    if "raw_task" in usage:
        raw_task = _as_dict(usage["raw_task"])
        if raw_task is not None and "usage" in raw_task:
            inner = _as_dict(raw_task["usage"])
            if inner is not None:
                merged.update(inner)
    for key, value in usage.items():
        if key != "raw_task":
            merged[key] = value
    return merged


def _usage_int(usage: dict[str, object], key: str) -> int | None:
    if key not in usage:
        return None
    return _as_int(usage[key])


def _tier_from_usage(model: str, usage: dict[str, object], region: str) -> str | None:
    width = _usage_int(usage, "output_width")
    if width is None:
        width = _usage_int(usage, "width")
    height = _usage_int(usage, "output_height")
    if height is None:
        height = _usage_int(usage, "height")
    if width is not None and height is not None and width > 0 and height > 0:
        return "2k" if width * height > TIER_1K_MAX_AREA else "1k"
    if "output_image_type" in usage:
        kind = usage["output_image_type"]
        if isinstance(kind, str):
            low = kind.lower()
            if "2k" in low:
                return "2k"
            if "1k" in low:
                return "1k"
    rate = _rate(model, region)
    if rate is not None and rate.output_1k == rate.output_2k:
        return "1k"
    return None


def settle_qwen_image_points(
    model: str,
    usage: dict[str, object],
    *,
    num_input_images: int = 0,
    base_url: str | None = None,
) -> int | None:
    """有输出张数才改实扣;尺寸对不上 3.0 Pro 的两档时维持预扣。"""
    merged = _merged_usage(usage)
    output_count = _usage_int(merged, "output_image_count")
    if output_count is None:
        output_count = _usage_int(merged, "image_count")
    if output_count is None or output_count <= 0:
        return None
    region = qwen_image_region(_resolved_base(base_url))
    tier = _tier_from_usage(model, merged, region)
    if tier is None:
        return None
    incoming = _usage_int(merged, "input_image_count")
    if incoming is None:
        incoming = num_input_images
    return calculate_qwen_image_points(
        model,
        tier=tier,
        num_input_images=max(0, incoming),
        output_count=output_count,
        region=region,
    )


__all__ = [
    "calculate_qwen_image_points",
    "estimate_qwen_image_points",
    "prepaid_tier",
    "qwen_image_region",
    "settle_qwen_image_points",
]
