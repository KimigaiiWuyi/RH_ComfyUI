"""按供应商原始响应用量回算历史任务积分(Seedance 2.x)

只处理统计表里 **成功且未退款、raw_response 能解析出 token** 的行。
与当前 ``point_cost`` 只做差额:多退少补,禁止按实扣再全额扣一次。
``apply=False`` 只预览;``adjust_wallet=True`` 时同步改 RHBind。
可重复执行(已对齐则跳过)。

调用方若另有任务表,用返回的 ``changes[].trace_id`` 自行回写。
"""

from __future__ import annotations

import re
import json
import asyncio
from typing import Any, Optional

from gsuid_core.logger import logger

from ...utils.mappers.seedance_billing import (
    extract_usage_tokens,
    settle_seedance2_points,
    settle_seedance25_points,
    has_input_from_vendor_tokens,
)

DEFAULT_SEEDANCE_RECONCILE_MODELS: tuple[str, ...] = ("seedance2", "seedance2.5")

_TOKEN_KEY_RE = re.compile(
    r'"(?:completion_tokens|total_tokens|totalTokens|completionTokens)"\s*:\s*(\d+)',
    re.IGNORECASE,
)

_SETTLE_FNS = {
    "seedance2": settle_seedance2_points,
    "seedance2.5": settle_seedance25_points,
}

_BATCH = 80
_MAX_CHANGES = 5000

_lock = asyncio.Lock()


def _parse_json_object(raw: Any) -> Optional[dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def usage_from_vendor_raw(raw: Any) -> Optional[dict[str, Any]]:
    """从厂商原始响应(或截断文本)取出可供 ``extract_usage_tokens`` 的 dict。"""
    obj = _parse_json_object(raw)
    if obj is not None:
        blobs: list[dict[str, Any]] = [obj]
        data = obj.get("data")
        if isinstance(data, dict):
            blobs.append(data)
        result = obj.get("result")
        if isinstance(result, dict):
            blobs.append(result)
        for blob in blobs:
            for key in ("usage", "tokenUsage", "token_usage"):
                u = blob.get(key)
                if isinstance(u, dict) and u:
                    return u
            if extract_usage_tokens(blob) is not None:
                return blob
    if isinstance(raw, str):
        nums = [int(m.group(1)) for m in _TOKEN_KEY_RE.finditer(raw)]
        nums = [n for n in nums if n > 0]
        if nums:
            return {"completion_tokens": max(nums), "total_tokens": max(nums)}
    return None


def _as_list(val: Any) -> list[Any]:
    return list(val) if isinstance(val, list) else []


def _content_has_video(body: dict[str, Any]) -> bool:
    for key in ("content", "ordered_content", "video_refs"):
        items = body.get(key)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, (int, float)) and not isinstance(it, bool):
                if float(it) > 0:
                    return True
                continue
            if not isinstance(it, dict):
                return True
            typ = str(it.get("type") or it.get("kind") or "").lower()
            if "video" in typ:
                return True
            if it.get("video_url") or it.get("videoUrl") or it.get("url"):
                if "video" in typ or key == "video_refs":
                    return True
            if it.get("role") in ("reference_video", "video"):
                return True
    return False


def vendor_output_meta(raw: Any) -> dict[str, Any]:
    """从厂商查询报文读实际 resolution / duration(优先于本地落库列)。"""
    obj = _parse_json_object(raw) or {}
    blobs: list[dict[str, Any]] = [obj]
    data = obj.get("data")
    if isinstance(data, dict):
        blobs.append(data)
    res = ""
    dur: Optional[float] = None
    for blob in blobs:
        if not res and blob.get("resolution"):
            res = str(blob.get("resolution") or "").strip()
        if dur is None:
            raw_d = blob.get("duration")
            try:
                val = float(raw_d) if raw_d is not None else 0.0
            except (TypeError, ValueError):
                val = 0.0
            if val > 0:
                dur = val
    return {"resolution": res, "duration": dur}


def request_bits_from_record(
    *,
    resolution: str = "",
    duration_seconds: Any = None,
    request_body: Any = None,
    extra_params: Any = None,
) -> dict[str, Any]:
    """从统计行还原 settle 所需的 resolution / 是否有输入视频。"""
    body = _parse_json_object(request_body) or {}
    extra = _parse_json_object(extra_params) or {}
    nested = body.get("params") if isinstance(body.get("params"), dict) else {}
    res = (
        extra.get("resolution")
        or nested.get("resolution")
        or body.get("resolution")
        or resolution
        or "720p"
    )
    res = str(res).strip() or "720p"
    ivd = extra.get("input_video_duration")
    if ivd is None:
        ivd = nested.get("input_video_duration")
    if ivd is None:
        ivd = body.get("input_video_duration")
    try:
        ivd_f = float(ivd) if ivd is not None else None
    except (TypeError, ValueError):
        ivd_f = None
    video_refs = _as_list(body.get("video_refs") or extra.get("video_refs") or nested.get("video_refs"))
    has_video = bool(video_refs) or (ivd_f is not None and ivd_f > 0) or _content_has_video(body)
    dur = extra.get("duration") or nested.get("duration") or body.get("duration") or duration_seconds
    try:
        dur_f = float(dur) if dur is not None else 5.0
    except (TypeError, ValueError):
        dur_f = 5.0
    if dur_f <= 0:
        dur_f = 5.0
    return {
        "resolution": res,
        "has_input": has_video,
        "input_video_duration": ivd_f if (ivd_f is not None and ivd_f > 0) else (1.0 if has_video else None),
        "video_refs": video_refs or ([1.0] if has_video else None),
        "duration": dur_f,
    }


def actual_points_for_seedance_record(
    *,
    task_name: str,
    raw_response: Any,
    resolution: str = "",
    duration_seconds: Any = None,
    request_body: Any = None,
    extra_params: Any = None,
) -> Optional[int]:
    """单条统计行 → 按供应商 token 的实扣积分;无法换算返回 None。"""
    fn = _SETTLE_FNS.get((task_name or "").strip())
    if fn is None:
        return None
    usage = usage_from_vendor_raw(raw_response)
    if usage is None:
        return None
    tokens = extract_usage_tokens(usage)
    if tokens is None:
        return None
    vendor = vendor_output_meta(raw_response)
    bits = request_bits_from_record(
        resolution=resolution,
        duration_seconds=duration_seconds,
        request_body=request_body,
        extra_params=extra_params,
    )
    res = str(vendor.get("resolution") or bits["resolution"] or "720p")
    out_dur = vendor.get("duration")
    if out_dur is None or float(out_dur) <= 0:
        out_dur = float(bits["duration"])
    inferred = has_input_from_vendor_tokens(int(tokens), res, float(out_dur or 0))
    has_input = bool(inferred) if inferred is not None else bool(bits["has_input"])
    refs = bits["video_refs"] if has_input else None
    ivd = bits["input_video_duration"] if has_input else None
    if has_input and not refs and ivd is None:
        ivd = 1.0
    return fn(
        usage,
        res,
        video_refs=refs,
        input_video_duration=ivd,
        output_duration=float(out_dur) if out_dur else None,
    )


def _row_plan(row: Any) -> Optional[dict[str, Any]]:
    prepaid = int(getattr(row, "point_cost", 0) or 0)
    actual = actual_points_for_seedance_record(
        task_name=str(getattr(row, "task_name", "") or ""),
        raw_response=getattr(row, "raw_response_json", "") or "",
        resolution=str(getattr(row, "resolution", "") or ""),
        duration_seconds=getattr(row, "duration_seconds", None),
        request_body=getattr(row, "request_body_json", "") or "",
        extra_params=getattr(row, "extra_params_json", "") or "",
    )
    if actual is None:
        return None
    delta = int(actual) - prepaid
    extra_obj = _parse_json_object(getattr(row, "extra_params_json", "") or "") or {}
    already_marked = extra_obj.get("usage_settled_points") is not None
    return {
        "record_id": int(getattr(row, "id", 0) or 0),
        "trace_id": str(getattr(row, "trace_id", "") or ""),
        "user_id": str(getattr(row, "user_id", "") or ""),
        "bot_id": str(getattr(row, "bot_id", "") or ""),
        "task_name": str(getattr(row, "task_name", "") or ""),
        "prepaid": prepaid,
        "actual": int(actual),
        "delta": delta,
        "wallet_marked": already_marked,
    }


async def _cas_point_cost(record_id: int, expected: int, new_cost: int) -> bool:
    from ...utils.database.models import RHComfyuiTaskRecord

    return bool(
        await RHComfyuiTaskRecord.cas_update_point_cost(
            int(record_id),
            expected=int(expected),
            new_cost=int(new_cost),
        )
    )


async def _apply_wallet(user_id: str, bot_id: str, delta: int, record_id: int) -> str:
    """返回 ok / denied / error / skipped。"""
    if delta == 0 or not user_id or user_id == "unknown":
        return "skipped"
    from .points_api import PointsDeniedError, charge_points, refund_points

    reason = f"usage_reconcile:record={record_id}"
    try:
        if delta > 0:
            await charge_points(user_id, bot_id or "", delta, reason=reason)
        else:
            await refund_points(user_id, bot_id or "", -delta, reason=reason)
        return "ok"
    except PointsDeniedError:
        return "denied"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[Billing.reconcile] 钱包差额失败 record={record_id} delta={delta}: {exc}")
        return "error"


async def reconcile_seedance_usage_billing(
    *,
    model_names: Optional[list[str] | tuple[str, ...]] = None,
    apply: bool = False,
    adjust_wallet: bool = True,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """扫描 Seedance 2.x 成功单,按原始 usage 回算 point_cost。

    Args:
        model_names: 默认 seedance2 / seedance2.5
        apply: False=只预览,True=写库(+可选钱包)
        adjust_wallet: 按差额改 RHBind;调用方若用独立账本应传 False,自行消化 changes
        limit: 最多处理多少条有用量的差额(不含 unchanged)
    """
    if _lock.locked():
        return {
            "ok": False,
            "error": "already_running",
            "message": "已有回算任务在执行,请稍后再试",
        }

    names = tuple(model_names) if model_names else DEFAULT_SEEDANCE_RECONCILE_MODELS
    names = tuple(n.strip() for n in names if str(n).strip())
    if not names:
        names = DEFAULT_SEEDANCE_RECONCILE_MODELS

    async with _lock:
        return await _reconcile_locked(
            names=names,
            apply=bool(apply),
            adjust_wallet=bool(adjust_wallet),
            limit=limit,
        )


async def _reconcile_locked(
    *,
    names: tuple[str, ...],
    apply: bool,
    adjust_wallet: bool,
    limit: Optional[int],
) -> dict[str, Any]:
    from sqlmodel import col, select

    from gsuid_core.utils.database.base_models import async_maker

    from ...utils.database.models import RHComfyuiTaskRecord

    scanned = 0
    with_usage = 0
    unchanged = 0
    updated = 0
    skipped_no_usage = 0
    wallet_denied = 0
    wallet_error = 0
    errors = 0
    charged_points = 0
    refunded_points = 0
    changes: list[dict[str, Any]] = []
    last_id = 0
    cap = int(limit) if isinstance(limit, int) and limit > 0 else None

    while True:
        async with async_maker() as session:
            stmt = (
                select(RHComfyuiTaskRecord)
                .where(
                    col(RHComfyuiTaskRecord.task_name).in_(list(names)),
                    col(RHComfyuiTaskRecord.status) == "ok",
                    col(RHComfyuiTaskRecord.refunded) == False,  # noqa: E712
                    col(RHComfyuiTaskRecord.id) > last_id,
                    col(RHComfyuiTaskRecord.raw_response_json) != "",
                )
                .order_by(col(RHComfyuiTaskRecord.id))
                .limit(_BATCH)
            )
            rows = list((await session.execute(stmt)).scalars().all())
        if not rows:
            break

        for row in rows:
            last_id = int(row.id or 0)
            scanned += 1
            try:
                plan = _row_plan(row)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning(f"[Billing.reconcile] 解析失败 id={row.id}: {exc}")
                continue
            if plan is None:
                skipped_no_usage += 1
                continue
            with_usage += 1
            if cap is not None and len(changes) >= cap:
                continue
            if not apply:
                if plan["delta"] == 0:
                    unchanged += 1
                else:
                    changes.append(plan)
                continue

            try:
                from ...utils.database.models import RHWalletOperation

                managed = await RHWalletOperation.reconcile_record_once(
                    plan["record_id"], plan["actual"], adjust_wallet=adjust_wallet,
                )
                if managed is not None:
                    receipt, replayed = managed
                    wallet_status = "receipt_managed"
                    if adjust_wallet:
                        wallet_status = "denied" if receipt.status == "declined" else "ok"
                        if receipt.status == "declined":
                            wallet_denied += 1
                        elif not replayed:
                            updated += 1
                            charged_points += max(receipt.billed_delta_points, 0)
                            refunded_points += max(-receipt.billed_delta_points, 0)
                    if len(changes) < _MAX_CHANGES:
                        changes.append({
                            **plan, "actual": receipt.net_after_points,
                            "delta": 0 if replayed else receipt.billed_delta_points,
                            "wallet": wallet_status, "operation_key": receipt.operation_key,
                            "receipt_digest": receipt.receipt_digest,
                        })
                    continue
                # 只有未接入操作回执的历史记录继续走旧兼容路径。
                if plan["delta"] == 0:
                    unchanged += 1
                    continue
                # SQLite UPDATE rowcount 不可靠,必须走 ORM CAS。
                ok = await _cas_point_cost(plan["record_id"], plan["prepaid"], plan["actual"])
                if not ok:
                    errors += 1
                    continue
                wallet_st = "skipped"
                if adjust_wallet and not plan.get("wallet_marked"):
                    wallet_st = await _apply_wallet(
                        plan["user_id"], plan["bot_id"], plan["delta"], plan["record_id"]
                    )
                    if wallet_st == "denied":
                        wallet_denied += 1
                    elif wallet_st == "error":
                        wallet_error += 1
                updated += 1
                if wallet_st == "ok":
                    if plan["delta"] > 0:
                        charged_points += plan["delta"]
                    else:
                        refunded_points += -plan["delta"]
                if len(changes) < _MAX_CHANGES:
                    item = dict(plan)
                    item["wallet"] = wallet_st
                    changes.append(item)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning(f"[Billing.reconcile] 回写失败 id={plan['record_id']}: {exc}")
                continue

        await asyncio.sleep(0)

    if apply and updated:
        try:
            from ...utils.database.stats_cache import invalidate_stats_cache

            await invalidate_stats_cache()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Billing.reconcile] 刷新统计缓存失败: {exc}")

    logger.info(
        f"[Billing.reconcile] models={list(names)} apply={apply} scanned={scanned} "
        f"usage={with_usage} updated={updated} unchanged={unchanged} "
        f"no_usage={skipped_no_usage} denied={wallet_denied}"
    )
    return {
        "ok": True,
        "apply": apply,
        "adjust_wallet": adjust_wallet,
        "models": list(names),
        "scanned": scanned,
        "with_usage": with_usage,
        "unchanged": unchanged,
        "updated": updated if apply else len(changes),
        "skipped_no_usage": skipped_no_usage,
        "wallet_denied": wallet_denied,
        "wallet_error": wallet_error,
        "errors": errors,
        "charged_points": charged_points if apply else sum(max(c["delta"], 0) for c in changes),
        "refunded_points": refunded_points if apply else sum(max(-c["delta"], 0) for c in changes),
        "changes": changes,
    }


__all__ = [
    "DEFAULT_SEEDANCE_RECONCILE_MODELS",
    "usage_from_vendor_raw",
    "request_bits_from_record",
    "actual_points_for_seedance_record",
    "reconcile_seedance_usage_billing",
]
