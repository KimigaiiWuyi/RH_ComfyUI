"""上游「实际上网」审计捕获 — 统计落库用最终 prompt / 请求体

``begin_task`` 时只能记下调用方入参;各 backend 在真正 POST 上游之前会改写
提示词(如 Seedance 引用语法)并拼装 HTTP body。本模块用 ContextVar 在当次
dispatch 协程内暂存那份**最终**载荷,由 ``record_task`` 优先写入:

- ``RHComfyuiTaskRecord.prompt``
- ``RHComfyuiTaskRecord.request_body_json``
- ``RHComfyuiTaskRecord.raw_response_json``(``set_vendor_raw``:bytes / base64 换成摘要,URL 保留)

约定:
- 进程内有效;backend 在 ``render_create`` / 等价步骤成功后调用 ``set_wire_audit``
- 同步镜像到 ``ActiveGeneration``(同 asyncio.Task),子协程 ContextVar 未继承时
  仍可从 active registry 读回
- 写入时统一 ``mask_body``:bytes 变成 ``<bytes len=N>``,base64 / data URL 变成摘要
"""

from __future__ import annotations

from typing import Any, Optional
from contextvars import ContextVar

_wire: ContextVar[Optional[dict[str, Any]]] = ContextVar("rh_comfyui_wire_audit", default=None)
_vendor: ContextVar[Optional[dict[str, object]]] = ContextVar("rh_comfyui_vendor_raw", default=None)

# 这些键是图片/音频正文,落统计会把 64KB 上限撑满
_BLOB_KEYS = frozenset({"b64_json", "image_base64", "ResultImage", "file_content"})
_MAX_STR = 12000


def _is_http_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def slim_vendor_json(value: object, *, key: str = "") -> object:
    """截断超长纯文本。bytes / base64 由 ``set_vendor_raw`` 先按请求体规则脱敏。"""
    if key in _BLOB_KEYS and not _is_http_url(value):
        return "<omitted>"
    if isinstance(value, (bytes, bytearray)):
        return f"<omitted {len(value)} bytes>"
    if isinstance(value, str):
        if len(value) > _MAX_STR:
            return f"<omitted {len(value)} chars>"
        return value
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for raw_key, item in value.items():
            if isinstance(raw_key, str):
                out[raw_key] = slim_vendor_json(item, key=raw_key)
        return out
    if isinstance(value, list):
        return [slim_vendor_json(item, key=key) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return type(value).__name__


def clear_wire_audit() -> None:
    """清空当前协程的 wire 快照(dispatch 开始/结束时调用)。"""
    _wire.set(None)
    _vendor.set(None)
    try:
        from ..dispatch.active_tasks import get_active_task_registry

        ag = get_active_task_registry().current()
        if ag is not None:
            ag.wire_prompt = None
            ag.wire_request = None
            ag.vendor_raw = None
    except Exception:  # noqa: BLE001 — 清理不得影响主流程
        pass


def _mirror_wire_to_active(cur: dict[str, Any]) -> None:
    """把 wire 镜像到当前 ActiveGeneration(ContextVar 兜底)。"""
    try:
        from ..dispatch.active_tasks import get_active_task_registry

        ag = get_active_task_registry().current()
        if ag is None:
            return
        if "prompt" in cur and isinstance(cur["prompt"], str):
            ag.wire_prompt = cur["prompt"]
        if "request" in cur:
            ag.wire_request = cur["request"]
    except Exception:  # noqa: BLE001 — 镜像失败不阻断 set_wire
        pass


def set_wire_audit(
    *,
    prompt: Optional[str] = None,
    request: Any = None,
    merge: bool = True,
) -> None:
    """记录即将发往上游的最终 prompt / 请求体。

    Args:
        prompt: 最终文本提示(已做供应商侧改写)
        request: 最终请求体。bytes / base64 在这里脱敏,调用方传原始载荷
        merge: True 时与已有快照合并;False 则整表覆盖
    """
    if merge:
        cur = dict(_wire.get() or {})
    else:
        cur = {}
    if prompt is not None:
        cur["prompt"] = prompt
    if request is not None:
        # 占位只在这一处做,避免各通道手写摘要时把图丢掉
        from ...utils.core.safe_json import mask_body

        cur["request"] = mask_body(request)
    _wire.set(cur)
    _mirror_wire_to_active(cur)


def get_wire_audit() -> dict[str, Any]:
    """返回 wire 快照:ContextVar 优先,缺项时补 ActiveGeneration 镜像。"""
    cur = dict(_wire.get() or {})
    try:
        from ..dispatch.active_tasks import get_active_task_registry

        ag = get_active_task_registry().current()
        if ag is None:
            return cur
        prompt = cur.get("prompt")
        if not (isinstance(prompt, str) and prompt.strip()) and isinstance(ag.wire_prompt, str):
            cur["prompt"] = ag.wire_prompt
        if cur.get("request") is None and ag.wire_request is not None:
            cur["request"] = ag.wire_request
    except Exception:  # noqa: BLE001
        pass
    return cur


def extract_prompt_from_request_body(body: Any) -> str:
    """从常见上游 body 形状提取可读 prompt 文本。

    支持:
    - ``{"prompt": "..."}``
    - ``{"content": [{"type":"text","text":"..."}, ...]}``(方舟 Seedance 等)
    - ``{"input": {"prompt": "..."}}`` 等一层嵌套
    """
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""

    p = body.get("prompt")
    if isinstance(p, str) and p.strip():
        return p

    content = body.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text")
                if isinstance(t, str) and t:
                    parts.append(t)
        if parts:
            return "".join(parts)

    for nest_key in ("input", "parameters", "payload", "data"):
        nested = body.get(nest_key)
        if isinstance(nested, dict):
            inner = extract_prompt_from_request_body(nested)
            if inner:
                return inner
    return ""


def set_wire_from_http_body(body: Any, *, prompt: Optional[str] = None) -> None:
    """便捷:从 HTTP JSON body 写入 wire(自动抽 prompt)。"""
    text = prompt if prompt is not None else extract_prompt_from_request_body(body)
    set_wire_audit(prompt=text or None, request=body)


def _mirror_vendor(cur: dict[str, object]) -> None:
    try:
        from ..dispatch.active_tasks import get_active_task_registry

        ag = get_active_task_registry().current()
        if ag is not None:
            ag.vendor_raw = dict(cur)
    except Exception:  # noqa: BLE001 — 镜像失败不阻断落库
        pass


def set_vendor_raw(payload: object) -> None:
    """记下厂商原始响应。bytes / base64 与请求体同一套脱敏,普通 URL 保留。"""
    from ...utils.core.safe_json import mask_body

    slimmed = slim_vendor_json(mask_body(payload))
    if not isinstance(slimmed, dict) or not slimmed:
        return
    _vendor.set(slimmed)
    _mirror_vendor(slimmed)


def attach_file_url(raw: dict[str, object], file_url: str) -> dict[str, object]:
    """厂商回包之外保留实际下载用的 file_url。"""
    merged = dict(raw)
    merged["file_url"] = file_url
    return merged


def get_vendor_raw() -> dict[str, object]:
    """ContextVar 优先;wait_for 子任务写过的回包从 ActiveGeneration 补回。"""
    cur = _vendor.get()
    if cur:
        return dict(cur)
    try:
        from ..dispatch.active_tasks import get_active_task_registry

        ag = get_active_task_registry().current()
        if ag is not None and ag.vendor_raw:
            return dict(ag.vendor_raw)
    except Exception:  # noqa: BLE001
        pass
    return {}


__all__ = [
    "clear_wire_audit",
    "set_wire_audit",
    "get_wire_audit",
    "extract_prompt_from_request_body",
    "set_wire_from_http_body",
    "slim_vendor_json",
    "set_vendor_raw",
    "get_vendor_raw",
    "attach_file_url",
]
