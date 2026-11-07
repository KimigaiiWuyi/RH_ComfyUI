"""fal 积分换算(纯计算,不读配置不发网络,供校验与计费引用)。

FLUX 3 Max 官方按「生成秒数 × 分辨率档」计费(美元):
  720p $0.17/s,1080p $0.29/s,时长整数秒 5~20。
MiniMax H3 Max / Turbo 同为「生成秒数 × 分辨率档」,时长浮点秒 0.92~15:
  H3 Max       480p $0.05/s,768p $0.08/s,1080p $0.16/s
  H3 Max Turbo 480p $0.025/s,768p $0.04/s,1080p $0.08/s(文生/图生半价)
  reference-to-video 两模型同价(按 H3 Max 档),另含 4096 个免费
  reference token,超出部分 H3 Max $0.02/1K、Turbo $0.016/1K。
积分换算沿用平台口径 $1 = 100 积分,向上取整、下限 1 分。

官网有限时阶梯折扣。折扣是限时的,这里始终按刊例价计——上游打折时
平台少付,刊例价计价对用户仍是正确的上限,不会欠收。
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal

from .catalog import MAX_DURATION, MIN_DURATION, H3_MAX_DURATION, H3_MIN_DURATION

# 积分 ↔ 美元:沿用平台 $1 = 100 积分口径
POINTS_PER_USD = Decimal("100")

# 官方刊例价(美元 / 秒),两档分辨率
RATE_PER_SECOND: dict[str, Decimal] = {
    "720p": Decimal("0.17"),
    "1080p": Decimal("0.29"),
}

# H3 刊例价:t2v / i2v 端点按模型分档
H3_RATE_PER_SECOND: dict[str, dict[str, Decimal]] = {
    "h3max": {
        "480p": Decimal("0.05"),
        "768p": Decimal("0.08"),
        "1080p": Decimal("0.16"),
    },
    "h3maxturbo": {
        "480p": Decimal("0.025"),
        "768p": Decimal("0.04"),
        "1080p": Decimal("0.08"),
    },
}

# reference-to-video 两个模型同按 H3 Max 档计(官方文档明示)
H3_REFERENCE_RATE: dict[str, Decimal] = H3_RATE_PER_SECOND["h3max"]

# reference token:每请求含 4096 个,超出按千 token 计(美元)
H3_REF_TOKENS_INCLUDED = 4096
H3_REF_TOKEN_USD_PER_1K: dict[str, Decimal] = {
    "h3max": Decimal("0.02"),
    "h3maxturbo": Decimal("0.016"),
}


def usd_to_points(usd: Decimal) -> int:
    cents = (usd * POINTS_PER_USD).quantize(Decimal("1"), rounding=ROUND_UP)
    return max(int(cents), 1)


def video_usd(resolution: str, seconds: int | float | str) -> Decimal:
    res = resolution.strip().lower()
    if res not in RATE_PER_SECOND:
        raise ValueError(f"flux3max 未知分辨率: {resolution}")
    secs = Decimal(str(seconds))
    if secs <= 0:
        raise ValueError(f"flux3max 时长必须为正: {seconds}")
    return RATE_PER_SECOND[res] * secs


def video_point_bounds() -> tuple[int, int]:
    """(min, max):5s 720p 下限,20s 1080p 上限。"""
    low = usd_to_points(video_usd("720p", MIN_DURATION))
    high = usd_to_points(video_usd("1080p", MAX_DURATION))
    return low, max(high, low + 1)


def h3_rate_for(model_name: str, mode: str) -> dict[str, Decimal]:
    """按模式取每秒价目表:reference 两家同按 H3 Max 档。"""
    if mode == "reference_to_video":
        return H3_REFERENCE_RATE
    return H3_RATE_PER_SECOND[model_name]


def h3_video_usd(model_name: str, mode: str, resolution: str, seconds: int | float | Decimal | str) -> Decimal:
    """H3 基础费 = 每秒刊例价 × 请求时长(reference 的 token 超量另计)。"""
    rates = h3_rate_for(model_name, mode)
    res = resolution.strip().lower()
    if res not in rates:
        raise ValueError(f"{model_name} 未知分辨率: {resolution}")
    secs = Decimal(str(seconds))
    if secs <= 0:
        raise ValueError(f"{model_name} 时长必须为正: {seconds}")
    return rates[res] * secs


def h3_ref_tokens_usd(model_name: str, tokens: int | float | Decimal) -> Decimal:
    """reference token 超量费:<=4096 免费,超出按 $x/1K prorated。"""
    over = Decimal(str(tokens)) - Decimal(H3_REF_TOKENS_INCLUDED)
    if over <= 0:
        return Decimal("0")
    return over / Decimal("1000") * H3_REF_TOKEN_USD_PER_1K[model_name]


def h3_point_bounds(model_name: str) -> tuple[int, int]:
    """(min, max):时长档位两端 × 最低/最高分辨率。

    reference token 超量费不设上限体现在区间里(取决于素材量),
    max 只计每秒价 × 15s。
    """
    low = usd_to_points(h3_video_usd(model_name, "text_to_video", "480p", H3_MIN_DURATION))
    high = usd_to_points(h3_video_usd(model_name, "reference_to_video", "1080p", H3_MAX_DURATION))
    return low, max(high, low + 1)
