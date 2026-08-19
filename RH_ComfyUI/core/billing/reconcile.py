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
    _RESOLUTION_SPECS,
    _calculate_tokens,
    extract_usage_tokens,
    settle_seedance2_points,
    settle_seedance25_points,
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


def _infer_has_input_from_tokens(resolution: str, duration: float, tokens: int, explicit: bool) -> bool:
    if explicit:
        return True
    spec = _RESOLUTION_SPECS.get(resolution) or _RESOLUTION_SPECS.get("720p")
    if spec is None:
        return False
    w, h, fps = spec
    out_tokens = _calculate_tokens(0.0, max(duration, 1.0), w, h, fps)
    if out_tokens <= 0:
        return False
    return tokens > out_tokens * 1.3


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
    bits = request_bits_from_record(
        resolution=resolution,
        duration_seconds=duration_seconds,
        request_body=request_body,
        extra_params=extra_params,
    )
    has_input = _infer_has_input_from_tokens(
        str(bits["resolution"]),
        float(bits["duration"]),
        int(tokens),
        bool(bits["has_input"]),
    )
    refs = bits["video_refs"] if has_input else None
    ivd = bits["input_video_duration"] if has_input else None
    if has_input and not refs and ivd is None:
        ivd = 1.0
    return fn(usage, str(bits["resolution"]), video_refs=refs, input_video_duration=ivd)


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
    return {
        "record_id": int(getattr(row, "id", 0) or 0),
        "trace_id": str(getattr(row, "trace_id", "") or ""),
        "user_id": str(getattr(row, "user_id", "") or ""),
        "bot_id": str(getattr(row, "bot_id", "") or ""),
        "task_name": str(getattr(row, "task_name", "") or ""),
        "prepaid": prepaid,
        "actual": int(actual),
        "delta": delta,
    }


async def _cas_point_cost(record_id: int, expected: int, new_cost: int) -> bool:
    from sqlmodel import col
    from sqlalchemy import update

    from gsuid_core.utils.database.base_models import async_maker

    from ...utils.database.models import RHComfyuiTaskRecord

    async with async_maker() as session:
        result = await session.execute(
            update(RHComfyuiTaskRecord)
            .where(
                col(RHComfyuiTaskRecord.id) == int(record_id),
                col(RHComfyuiTaskRecord.point_cost) == int(expected),
                col(RHComfyuiTaskRecord.status) == "ok",
            )
            .values(point_cost=int(new_cost))
        )
        await session.commit()
        return int(getattr(result, "rowcount", 0) or 0) > 0


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
            if plan["delta"] == 0:
                unchanged += 1
                continue
            if cap is not None and len(changes) >= cap:
                continue
            if not apply:
                changes.append(plan)
                continue

            wallet_st = "skipped"
            if adjust_wallet:
                wallet_st = await _apply_wallet(
                    plan["user_id"], plan["bot_id"], plan["delta"], plan["record_id"]
                )
                if wallet_st == "denied":
                    wallet_denied += 1
                    continue
                if wallet_st == "error":
                    wallet_error += 1
                    continue
            ok = await _cas_point_cost(plan["record_id"], plan["prepaid"], plan["actual"])
            if not ok:
                if wallet_st == "ok":
                    await _apply_wallet(
                        plan["user_id"],
                        plan["bot_id"],
                        -int(plan["delta"]),
                        plan["record_id"],
                    )
                errors += 1
                continue
            updated += 1
            if plan["delta"] > 0:
                charged_points += plan["delta"]
            else:
                refunded_points += -plan["delta"]
            if len(changes) < _MAX_CHANGES:
                item = dict(plan)
                item["wallet"] = wallet_st
                changes.append(item)

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
