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

from typing import Any, Optional

from gsuid_core.logger import logger


def invoke_settle_cost(model: Any, request: Any, usage: Optional[dict[str, Any]]) -> Optional[int]:
    """调用模型 ``settle_cost``;失败/空值 → None(维持预扣)。"""
    fn = getattr(model, "settle_cost", None)
    if not callable(fn):
        return None
    try:
        raw = fn(request, usage or {})
    except Exception as e:  # noqa: BLE001 — 后结算失败不得打翻已成功的生成
        name = getattr(model, "name", type(model).__name__)
        logger.warning(f"[Billing] {name} settle_cost 失败,维持预扣: {e}")
        return None
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def request_from_settle_payload(params: Optional[dict[str, Any]], *, model: str = "") -> Any:
    """从 job.params / GenerateRequest dump 构造最小 GenerationRequest(仅供 settle_cost)。"""
    from ..schema.request import TaskType, GenerationRequest

    p = dict(params or {})
    nested = p.get("params") if isinstance(p.get("params"), dict) else {}
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
    video_refs = p.get("video_refs") or nested.get("video_refs") or []
    if not isinstance(video_refs, list):
        video_refs = []
    duration = p.get("duration")
    if duration is None:
        duration = merged.get("duration", 5)
    try:
        duration_i = int(duration)
    except (TypeError, ValueError):
        duration_i = 5
    resolution = p.get("resolution") or merged.get("resolution")
    ga = p.get("generate_audio")
    if ga is None:
        ga = merged.get("generate_audio", True)
    return GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=str(p.get("prompt") or ""),
        resolution=str(resolution) if resolution else None,
        duration=duration_i,
        video_refs=video_refs,
        params=merged,
        model=model or str(p.get("model") or "") or None,
        generate_audio=bool(ga),
    )


def settle_model_cost(
    model_name: str,
    usage: Optional[dict[str, Any]] = None,
    *,
    request: Any = None,
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
