"""后结算 — 预扣后按供应商用量对齐(只做差额,禁止双重扣费)

``estimate_cost`` 决定 reserve 金额;成功后模型可覆盖 ``settle_cost(request, usage)``
把供应商 ``usage``(如 Seedance ``total_tokens``)换成实扣积分。dispatcher 把该值
交给 ``BillingPolicy.settle(reservation, actual)``:

- ``actual is None`` → 预扣即终扣
- ``actual != prepaid`` → **只补/退差额**,绝不按 actual 再扣一遍
- HTTP / ExternalPrepaid:引擎不碰钱包,只把 ``result.cost_points`` 写成实扣,
  由调用方对已预扣账本做同样的差额对齐
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from gsuid_core.logger import logger

from ..schema.request import TaskType, GenerationRequest


def _positive_int(raw: object) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, (int, float, str)):
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None
    return None


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


@runtime_checkable
class _SettlesCost(Protocol):
    def settle_cost(self, request: object, usage: dict[str, Any]) -> object: ...


@runtime_checkable
class _Named(Protocol):
    name: str


def _coerce_int(raw: object, default: int) -> int:
    if isinstance(raw, (int, float, str)):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return default


def invoke_settle_cost(model: object, request: object, usage: Optional[dict[str, Any]]) -> Optional[int]:
    """调用模型 ``settle_cost``;失败/空值 → None(维持预扣)。"""
    if not isinstance(model, _SettlesCost):
        return None
    try:
        raw = model.settle_cost(request, usage or {})
    except Exception as e:  # noqa: BLE001 — 后结算失败不得打翻已成功的生成
        name = model.name if isinstance(model, _Named) and model.name else type(model).__name__
        logger.warning(f"[Billing] {name} settle_cost 失败,维持预扣: {e}")
        return None
    return _positive_int(raw)


def request_from_settle_payload(params: Optional[dict[str, Any]], *, model: str = "") -> GenerationRequest:
    """从调用方 params / 请求 dump 构造最小 GenerationRequest(仅供 settle_cost)。"""
    p = _as_dict(params)
    nested = _as_dict(p["params"] if "params" in p else None)
    merged: dict[str, Any] = dict(nested)
    for key in (
        "resolution",
        "duration",
        "generate_audio",
        "input_video_duration",
        "ratio",
    ):
        if key in p and p[key] is not None:
            merged.setdefault(key, p[key])
    raw_refs: Any = p["video_refs"] if "video_refs" in p else None
    if not raw_refs:
        raw_refs = nested["video_refs"] if "video_refs" in nested else []
    video_refs: list[Any] = raw_refs if isinstance(raw_refs, list) else []
    duration = p["duration"] if "duration" in p else None
    if duration is None:
        duration = merged["duration"] if "duration" in merged else 5
    resolution = p["resolution"] if "resolution" in p else None
    if not resolution:
        resolution = merged["resolution"] if "resolution" in merged else None
    ga = p["generate_audio"] if "generate_audio" in p else None
    if ga is None:
        ga = merged["generate_audio"] if "generate_audio" in merged else True
    raw_prompt = p["prompt"] if "prompt" in p else None
    raw_model = p["model"] if "model" in p else None
    return GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=str(raw_prompt or ""),
        resolution=str(resolution) if resolution else None,
        duration=_coerce_int(duration, 5),
        video_refs=video_refs,
        params=merged,
        model=model or str(raw_model or "") or None,
        generate_audio=bool(ga),
    )


def settle_model_cost(
    model_name: str,
    usage: Optional[dict[str, Any]] = None,
    *,
    request: object = None,
    params: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """按模型 + 供应商 usage 计算实扣积分。模型未覆盖 settle_cost 时返回 None。"""
    from ..routing.registry import model_registry

    m = model_registry.get(model_name)
    if m is None:
        return None
    req = request
    if req is None:
        req = request_from_settle_payload(params, model=model_name)
    return invoke_settle_cost(m, req, usage)


__all__ = [
    "invoke_settle_cost",
    "request_from_settle_payload",
    "settle_model_cost",
]
