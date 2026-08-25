"""额度档位: special / unlimited 与三桶 cap 契约。"""

from RH_ComfyUI.core.billing.tier_quota import (
    TIER_KEYS,
    UNLIMITED_TIER,
    normalize_tier,
    get_tier_quotas,
    list_tier_quotas,
    is_unlimited_tier,
)
from RH_ComfyUI.rh_config.plugin_config import PLUGIN_CONFIG_DEFAULT


def test_tier_keys_include_special_and_unlimited():
    assert "special" in TIER_KEYS
    assert "unlimited" in TIER_KEYS
    assert UNLIMITED_TIER == "unlimited"


def test_normalize_accepts_new_tiers():
    assert normalize_tier("special") == "special"
    assert normalize_tier("SPECIAL") == "special"
    assert normalize_tier("unlimited") == "unlimited"
    assert normalize_tier("no-such") == "free"
    assert normalize_tier(None) == "free"


def test_special_quota_config_defaults_follow_5h_multiples():
    h5 = PLUGIN_CONFIG_DEFAULT["Quota_Special_5h"].data
    day = PLUGIN_CONFIG_DEFAULT["Quota_Special_Day"].data
    week = PLUGIN_CONFIG_DEFAULT["Quota_Special_Week"].data
    assert h5 == 500_000
    assert day == h5 * 4
    assert week == h5 * 12
    assert day == 2_000_000
    assert week == 6_000_000


def test_special_quotas_match_defaults_when_config_absent_or_default():
    q = get_tier_quotas("special")
    assert q.tier == "special"
    assert q.label == "特殊用户"
    assert q.unlimited is False
    assert q.h5 == 500_000
    assert q.day == 2_000_000
    assert q.week == 6_000_000
    dumped = q.as_dict()
    assert dumped["unlimited"] is False
    assert dumped["h5"] == 500_000


def test_unlimited_tier_flag_and_zero_caps():
    q = get_tier_quotas("unlimited")
    assert q.tier == "unlimited"
    assert q.label == "无上限用户"
    assert q.unlimited is True
    assert q.h5 == 0
    assert q.day == 0
    assert q.week == 0
    dumped = q.as_dict()
    assert dumped["unlimited"] is True
    assert is_unlimited_tier("unlimited") is True
    assert is_unlimited_tier("special") is False
    assert is_unlimited_tier("free") is False


def test_list_tier_quotas_includes_six_keys():
    listed = list_tier_quotas()
    assert set(listed) == set(TIER_KEYS)
    assert listed["unlimited"].unlimited is True
    assert listed["special"].h5 == 500_000
