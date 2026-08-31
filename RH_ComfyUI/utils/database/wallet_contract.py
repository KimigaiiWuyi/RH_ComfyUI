"""钱包操作命令与校验；不导入计费调度器或持有余额。"""

from __future__ import annotations

import re
import json
import hashlib
from typing import Literal
from dataclasses import dataclass

MAX_WALLET_POINTS = 2_000_000_000


class WalletIntegrityError(RuntimeError):
    """钱包候选或写入结果不唯一，事务必须回滚。"""


class WalletOperationConflict(ValueError):
    """幂等键或原始计费主体与已冻结命令不一致。"""


def validate_wallet_points(value: int) -> None:
    if type(value) is not int or not 0 <= value <= MAX_WALLET_POINTS:
        raise ValueError("points must be a bounded nonnegative integer")


@dataclass(frozen=True)
class WalletOperationCommand:
    operation_key: str
    job_key: str
    external_ref: str
    kind: Literal["charge", "settle", "refund"]
    user_id: str
    bot_id: str
    request_hash: str
    price_revision: str
    requested_target_points: int
    operation_version: int = 1
    vip_tier: str | None = None

    def __post_init__(self) -> None:
        from ...core.billing.tier_quota import normalize_tier

        validate_wallet_points(self.requested_target_points)
        if type(self.operation_version) is not int or self.operation_version != 1:
            raise ValueError("unsupported wallet operation version")
        if self.kind not in ("charge", "settle", "refund"):
            raise ValueError("unsupported wallet operation kind")
        if self.kind == "refund" and self.requested_target_points != 0:
            raise ValueError("refund target must be zero")
        for value in (self.operation_key, self.job_key, self.external_ref, self.user_id, self.bot_id):
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError("wallet identity must be a nonempty bounded string")
        if not isinstance(self.price_revision, str) or not 1 <= len(self.price_revision) <= 128:
            raise ValueError("price_revision is required")
        if not isinstance(self.request_hash, str) or re.fullmatch(r"[0-9a-f]{64}", self.request_hash) is None:
            raise ValueError("request_hash must be a full lowercase SHA256")
        if self.vip_tier is not None and (
            not isinstance(self.vip_tier, str) or self.vip_tier != normalize_tier(self.vip_tier)
        ):
            raise ValueError("unknown vip tier")

    @property
    def command_hash(self) -> str:
        payload = {
            "operation_key": self.operation_key,
            "job_key": self.job_key,
            "external_ref": self.external_ref,
            "kind": self.kind,
            "operation_version": self.operation_version,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "request_hash": self.request_hash,
            "price_revision": self.price_revision,
            "requested_target_points": self.requested_target_points,
            "vip_tier": self.vip_tier,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
