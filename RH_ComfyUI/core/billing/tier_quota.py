"""三重余额档位配置与时间边界。

产品模型(concurrent budgets):
  - 每用户每 bot_id 持有三桶余额:5h / day / week
  - 扣费 cost 同时从三桶各扣 cost;可用 = min(三桶)
  - 到期只补不降:把对应桶设为该档 config 满额

VIP 策略(与 bot_id 无关):
  - 档位存在 RHBind.vip_tier(或调用方显式传入 vip_tier)
  - free / basic / pro / enterprise 全入口通用(HTTP / bot / agent)
  - 额度数字权威:``PLUGIN_CONFIG`` 的 Quota_* 键
"""

from __future__ import annotations

import time
from typing import Any, Dict, Final, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from ...rh_config.comfyui_config import PLUGIN_CONFIG

# 常用 HTTP/业务入口 bot_id 约定值(任意 bot_id 均可;仅作文档化常量)
CANVAS_BOT_ID: Final[str] = "canvas"

TIER_KEYS: Final[tuple[str, ...]] = ("free", "basic", "pro", "enterprise")

_TIER_CONFIG: Final[Dict[str, tuple[str, str, str]]] = {
    "free": ("Quota_Free_5h", "Quota_Free_Day", "Quota_Free_Week"),
    "basic": ("Quota_Basic_5h", "Quota_Basic_Day", "Quota_Basic_Week"),
    "pro": ("Quota_Pro_5h", "Quota_Pro_Day", "Quota_Pro_Week"),
    "enterprise": ("Quota_Enterprise_5h", "Quota_Enterprise_Day", "Quota_Enterprise_Week"),
}

_TIER_FALLBACK: Final[Dict[str, tuple[int, int, int]]] = {
    "free": (8000, 20000, 80000),
    "basic": (20000, 50000, 200000),
    "pro": (40000, 100000, 400000),
    "enterprise": (80000, 200000, 800000),
}

_TIER_LABELS: Final[Dict[str, str]] = {
    "free": "免费用户",
    "basic": "基础会员",
    "pro": "专业会员",
    "enterprise": "企业会员",
}


@dataclass(frozen=True)
class TierQuotas:
    tier: str
    label: str
    h5: int
    day: int
    week: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "label": self.label,
            "h5": self.h5,
            "day": self.day,
            "week": self.week,
        }


def _cfg_int(key: str, default: int) -> int:
    try:
        raw = PLUGIN_CONFIG.get_config(key).data
        return max(int(raw), 0)
    except Exception:  # noqa: BLE001
        return max(default, 0)


def _cfg_str(key: str, default: str) -> str:
    try:
        raw = PLUGIN_CONFIG.get_config(key).data
        s = str(raw or "").strip()
        return s or default
    except Exception:  # noqa: BLE001
        return default


def get_quota_timezone() -> ZoneInfo:
    name = _cfg_str("Quota_Timezone", "Asia/Shanghai")
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Shanghai")


def get_5h_window_seconds() -> int:
    return max(_cfg_int("Quota_5h_Seconds", 18000), 60)


def normalize_tier(tier: Optional[str]) -> str:
    t = (tier or "free").strip().lower() or "free"
    return t if t in _TIER_CONFIG else "free"


def resolve_tier_for_billing(
    *,
    bot_id: str = "",
    vip_tier: Optional[str] = None,
) -> str:
    """解析计费档位。

    **与 bot_id 无关** — basic/pro/enterprise 在任意 bot 池生效。
    ``bot_id`` 参数仅保留兼容旧调用方,不参与判定。
    ``vip_tier`` 为空时回落 free(调用方应优先传入 RHBind 上已存的档)。
    """
    del bot_id  # 故意不用:档位不跟平台绑定
    return normalize_tier(vip_tier)


def get_tier_quotas(tier: Optional[str] = None) -> TierQuotas:
    t = normalize_tier(tier)
    k5, kd, kw = _TIER_CONFIG[t]
    f5, fd, fw = _TIER_FALLBACK[t]
    return TierQuotas(
        tier=t,
        label=_TIER_LABELS[t],
        h5=_cfg_int(k5, f5),
        day=_cfg_int(kd, fd),
        week=_cfg_int(kw, fw),
    )


def list_tier_quotas() -> Dict[str, TierQuotas]:
    return {t: get_tier_quotas(t) for t in TIER_KEYS}


def now_ts() -> int:
    return int(time.time())


def local_now(ts: Optional[int] = None) -> datetime:
    tz = get_quota_timezone()
    return datetime.fromtimestamp(ts if ts is not None else now_ts(), tz=tz)


def start_of_local_day(ts: Optional[int] = None) -> int:
    dt = local_now(ts)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def start_of_local_week(ts: Optional[int] = None) -> int:
    """周一 00:00(本地时区)作为一周起点。"""
    dt = local_now(ts)
    # Monday=0
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=dt.weekday())
    return int(start.timestamp())


def next_5h_refresh_at(timer_started_at_5h: int) -> int:
    """5h 下次补满时刻。

    ``timer_started_at_5h == 0`` 表示**尚未开始计时**(满额闲置),返回 0;
    前端应展示「使用后开始计时」而非倒计时。
    """
    base = int(timer_started_at_5h or 0)
    if base <= 0:
        return 0
    return base + get_5h_window_seconds()


def next_day_refresh_at(ts: Optional[int] = None) -> int:
    return start_of_local_day(ts) + 86400


def next_week_refresh_at(ts: Optional[int] = None) -> int:
    return start_of_local_week(ts) + 7 * 86400


def needs_5h_refresh(timer_started_at_5h: int, now: Optional[int] = None) -> bool:
    """5h 是否该补满。

    仅当**已开始计时**且经过窗口秒数才补满。
    timer=0(满额未使用)永不因时间流逝补满 —— 本来就满。
    """
    n = now if now is not None else now_ts()
    base = int(timer_started_at_5h or 0)
    if base <= 0:
        return False
    return (n - base) >= get_5h_window_seconds()


def needs_day_refresh(refreshed_at_day: int, now: Optional[int] = None) -> bool:
    n = now if now is not None else now_ts()
    base = int(refreshed_at_day or 0)
    if base <= 0:
        return True
    return start_of_local_day(base) < start_of_local_day(n)


def needs_week_refresh(refreshed_at_week: int, now: Optional[int] = None) -> bool:
    n = now if now is not None else now_ts()
    base = int(refreshed_at_week or 0)
    if base <= 0:
        return True
    return start_of_local_week(base) < start_of_local_week(n)


__all__ = [
    "CANVAS_BOT_ID",
    "TIER_KEYS",
    "TierQuotas",
    "get_tier_quotas",
    "list_tier_quotas",
    "normalize_tier",
    "resolve_tier_for_billing",
    "get_quota_timezone",
    "get_5h_window_seconds",
    "now_ts",
    "local_now",
    "start_of_local_day",
    "start_of_local_week",
    "next_5h_refresh_at",
    "next_day_refresh_at",
    "next_week_refresh_at",
    "needs_5h_refresh",
    "needs_day_refresh",
    "needs_week_refresh",
]
