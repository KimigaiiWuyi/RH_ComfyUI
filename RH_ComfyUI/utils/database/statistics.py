"""任务统计写入 — 模块函数,无 DI、无单例

生命周期:
  begin_task()   — 预扣后立刻插入 status=running(可在消费列表看到进行中)
  record_task()  — 终态:有 record_id 则 UPDATE,否则 INSERT(兼容旧调用 / begin 失败)

dispatch / execute_generation 的失败路径都调 record_task;写库失败仅打日志。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional
from pathlib import Path
from datetime import datetime, timezone

from gsuid_core.logger import logger

from ..core.request import TaskType
from ..core.safe_json import dump_body

if TYPE_CHECKING:
    from ..core.request import GenerationResult, GenerationRequest

# node 为 NodeDef 或 telemetry 的 duck-typed 视图(有 name/backend/point_cost 等)
NodeLike = Any

# ── 写入上限(防止数据库膨胀 / 长字符串拖垮 ORM) ──
RAW_RESPONSE_MAX_BYTES = 64 * 1024  # 厂商原始响应截断 64KB
EXTRA_PARAMS_MAX_BYTES = 2 * 1024  # 额外参数截断 2KB
ERROR_MESSAGE_MAX_CHARS = 2000  # 错误信息截断 2000 字符


def _safe_json_dumps(obj: Any, max_bytes: int) -> str:
    """把对象序列化为 JSON 字符串,超过 max_bytes 时截断尾部"""
    try:
        encoded_text = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded_text = repr(obj)
    encoded = encoded_text.encode("utf-8")
    if len(encoded) > max_bytes:
        # 预留尾部标记位
        encoded = encoded[: max_bytes - 32] + b"...[TRUNCATED]"
        return encoded.decode("utf-8", errors="ignore")
    return encoded_text


def _truncate_str(s: str, max_chars: int) -> str:
    """字符串超过 max_chars 时截断并追加 TRUNCATED 标记"""
    if len(s) > max_chars:
        return s[: max_chars - 16] + "...[TRUNCATED]"
    return s


def _relativize_saved_files(paths: Any) -> list[str]:
    """把 executor._save_output 写进 metadata 的落盘绝对路径转为相对 OUTPUT_PATH。"""
    if not isinstance(paths, (list, tuple)):
        return []
    try:
        from ..resource.RESOURCE_PATH import OUTPUT_PATH

        base = Path(OUTPUT_PATH).resolve()
    except Exception:  # noqa: BLE001 - 资源路径不可用时放弃记录,不影响主流程
        return []
    rels: list[str] = []
    for p in paths:
        try:
            rels.append(Path(str(p)).resolve().relative_to(base).as_posix())
        except (ValueError, OSError):
            continue
    return rels


# ── 各任务类型的核心输入参数提取器 ──


def _extract_video_params(request: "GenerationRequest") -> dict[str, Any]:
    return {
        "duration_seconds": request.duration,
        "width": request.width,
        "height": request.height,
        "ratio": request.ratio or "",
        "resolution": request.resolution or "",
        "seed": request.seed,
    }


def _extract_image_params(request: "GenerationRequest") -> dict[str, Any]:
    return {
        "duration_seconds": None,
        "width": request.width,
        "height": request.height,
        "ratio": "",
        "resolution": "",
        "seed": request.seed,
    }


def _extract_music_params(request: "GenerationRequest") -> dict[str, Any]:
    return {
        "duration_seconds": request.duration,
        "width": None,
        "height": None,
        "ratio": "",
        "resolution": "",
        "seed": None,
    }


def _extract_speech_params(request: "GenerationRequest") -> dict[str, Any]:
    return {
        "duration_seconds": None,
        "width": None,
        "height": None,
        "ratio": "",
        "resolution": "",
        "seed": None,
        "voice_id": request.voice_id or "",
    }


_EXTRACTORS: dict[TaskType, Any] = {
    TaskType.VIDEO: _extract_video_params,
    TaskType.IMAGE: _extract_image_params,
    TaskType.MUSIC: _extract_music_params,
    TaskType.SPEECH: _extract_speech_params,
}


def _extract_core_params(request: "GenerationRequest") -> dict[str, Any]:
    extractor: Any = _EXTRACTORS.get(request.task_type, lambda r: {})
    return extractor(request)


def _resolve_user_id(request: "GenerationRequest") -> str:
    user_id_raw = request.user_id
    return (user_id_raw if user_id_raw else "unknown")[:64]


def _resolve_provider_model(node: NodeLike) -> tuple[str, str]:
    """返回 (provider, model_id)。"""
    provider = getattr(node, "provider", None) or ""
    model_id = getattr(node, "backend_model", None) or ""
    backend_models = getattr(node, "backend_models", None) or {}
    if provider and backend_models:
        selected = backend_models.get(provider)
        if selected:
            model_id = selected
    return str(provider), str(model_id)


def _merged_extra_json(request: "GenerationRequest") -> str:
    merged_params: dict[str, Any] = {}
    if request.params:
        merged_params.update(request.params)
    if request.extra:
        merged_params.update(request.extra)
    if not merged_params:
        return ""
    return _safe_json_dumps(merged_params, EXTRA_PARAMS_MAX_BYTES)


def _build_common_insert_kwargs(
    *,
    request: "GenerationRequest",
    node: NodeLike,
    bot_id: str,
    group_id: str,
    trace_id: str,
    entry_point: str,
    backend_key_prefix: str,
    request_body: Optional[dict[str, Any]],
    status: str,
    elapsed_ms: Optional[int],
    point_cost: int,
    error: Optional[str] = None,
    raw_json: str = "",
    saved_files_json: str = "",
) -> dict[str, Any]:
    core = _extract_core_params(request)
    provider, model_id = _resolve_provider_model(node)
    request_body_json = dump_body(request_body if request_body is not None else request)
    return {
        "user_id": _resolve_user_id(request),
        "bot_id": bot_id[:64] if bot_id else "",
        "group_id": group_id[:64] if group_id else "",
        "task_type": request.task_type.value,
        "task_name": node.name[:128],
        "backend": node.backend[:32] if node.backend else "",
        "backend_model": model_id[:128],
        "backend_provider": provider[:32],
        "backend_key_prefix": backend_key_prefix[:16],
        "duration_seconds": core.get("duration_seconds"),
        "width": core.get("width"),
        "height": core.get("height"),
        "ratio": str(core.get("ratio", ""))[:16],
        "resolution": str(core.get("resolution", ""))[:16],
        "seed": core.get("seed"),
        "voice_id": str(core.get("voice_id", ""))[:64],
        "extra_params_json": _merged_extra_json(request),
        "prompt": _truncate_str(request.prompt or "", 4000),
        "status": status[:16],
        "elapsed_ms": elapsed_ms,
        "point_cost": int(point_cost or 0),
        "error_message": _truncate_str(error or "", ERROR_MESSAGE_MAX_CHARS),
        "raw_response_json": raw_json,
        "request_body_json": request_body_json,
        "trace_id": trace_id[:64] if trace_id else "",
        "created_at": datetime.now(timezone.utc),
        "entry_point": entry_point[:16] if entry_point else "",
        "saved_files_json": saved_files_json,
    }


# ── 核心入口 ──


async def begin_task(
    *,
    request: "GenerationRequest",
    node: NodeLike,
    bot_id: str = "",
    group_id: str = "",
    trace_id: str = "",
    entry_point: str = "",
    point_cost: int = 0,
    request_body: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """任务创建/预扣后立刻落库 status=running,返回 record id。

    失败返回 None,不抛出——调用方仍可在 record_task 时一次 INSERT 终态。
    """
    try:
        from .models import RHComfyuiTaskRecord, RHComfyuiTaskStatus

        kwargs = _build_common_insert_kwargs(
            request=request,
            node=node,
            bot_id=bot_id,
            group_id=group_id,
            trace_id=trace_id or (request.trace_id or ""),
            entry_point=entry_point,
            backend_key_prefix="",
            request_body=request_body,
            status=RHComfyuiTaskStatus.RUNNING.value,
            elapsed_ms=0,
            point_cost=point_cost if point_cost else int(node.point_cost or 0),
        )
        record_id = await RHComfyuiTaskRecord.insert_task_record(**kwargs)
        # running 行不标脏 stats：合计/榜单看终态；begin 每次 invalidate
        # 会让 /admin/stats 在任务进行中就扫全表。record_task 终态再标脏。
        logger.info(
            f"[RHComfyUI.Statistics] began id={record_id} task={node.name} "
            f"user={kwargs['user_id']} status=running cost={kwargs['point_cost']}"
        )
        return record_id
    except Exception as e:  # noqa: BLE001 - 统计失败不影响主流程
        logger.warning(f"[RHComfyUI.Statistics] begin_task 失败(已忽略): {e}")
        return None


def _resolve_wire_for_record(
    *,
    request: "GenerationRequest",
    request_body: Optional[dict[str, Any]],
    result: Optional["GenerationResult"],
) -> tuple[str, Any]:
    """解析最终落库的 prompt 与 request body。

    优先级:
      1. wire_capture(ContextVar + ActiveGeneration 镜像)
      2. result.metadata 中的 wire_prompt / wire_request
      3. 调用方原始 request_body / request.prompt
    """
    from ...core.telemetry.wire_capture import get_wire_audit

    wire = get_wire_audit()
    meta: dict[str, Any] = dict(result.metadata) if result is not None else {}

    wire_prompt = wire["prompt"] if "prompt" in wire else None
    meta_prompt = meta["wire_prompt"] if "wire_prompt" in meta else None
    if isinstance(wire_prompt, str) and wire_prompt.strip():
        prompt = wire_prompt
    elif isinstance(meta_prompt, str) and meta_prompt.strip():
        prompt = meta_prompt
    else:
        prompt = request.prompt or ""

    if "request" in wire and wire["request"] is not None:
        body: Any = wire["request"]
    elif "wire_request" in meta and meta["wire_request"] is not None:
        body = meta["wire_request"]
    elif request_body is not None:
        body = request_body
    else:
        body = request

    return prompt, body


async def record_task(
    *,
    request: "GenerationRequest",
    result: Optional["GenerationResult"],
    node: NodeLike,
    status: str,
    elapsed_ms: int,
    error: Optional[str] = None,
    bot_id: str = "",
    group_id: str = "",
    trace_id: str = "",
    entry_point: str = "",
    backend_key_prefix: str = "",
    request_body: Optional[dict[str, Any]] = None,
    record_id: Optional[int] = None,
    point_cost: Optional[int] = None,
) -> Optional[int]:
    """记录/更新任务终态。

    - ``record_id`` 有值:UPDATE 已有 running 行(创建时 begin_task 写入)
    - 否则:INSERT 终态行(兼容历史只在结束写一次的调用方;begin 失败时的回落)
    - prompt / request_body_json 优先写 **上游最终 wire**(见 wire_capture),
      而非调用方入参

    整体 try/except 兜底:写库失败仅 logger.warning,不影响主流程。
    """
    try:
        from .models import RHComfyuiTaskRecord

        raw_json = ""
        if result is not None and result.raw:
            raw_json = _safe_json_dumps(result.raw, RAW_RESPONSE_MAX_BYTES)

        saved_files_json = ""
        if result is not None and result.metadata:
            saved_paths = result.metadata.get("saved_files")
            if not saved_paths and result.metadata.get("saved_path"):
                saved_paths = [result.metadata["saved_path"]]
            rel_files = _relativize_saved_files(saved_paths)
            if rel_files:
                saved_files_json = _safe_json_dumps(rel_files, EXTRA_PARAMS_MAX_BYTES)

        cost = int(point_cost) if point_cost is not None else int(node.point_cost or 0)
        provider, model_id = _resolve_provider_model(node)
        error_msg = _truncate_str(error or "", ERROR_MESSAGE_MAX_CHARS)

        wire_prompt, wire_body = _resolve_wire_for_record(request=request, request_body=request_body, result=result)
        request_body_json = dump_body(wire_body)
        prompt_final = _truncate_str(wire_prompt or "", 4000)

        # 优先 UPDATE 已有 running 行
        if record_id is not None and int(record_id) > 0:
            ok = await RHComfyuiTaskRecord.update_task_record(
                int(record_id),
                status=status[:16],
                elapsed_ms=elapsed_ms,
                error_message=error_msg,
                raw_response_json=raw_json,
                saved_files_json=saved_files_json,
                backend=node.backend[:32] if node.backend else "",
                backend_model=model_id[:128],
                backend_provider=provider[:32],
                backend_key_prefix=backend_key_prefix[:16] if backend_key_prefix else "",
                point_cost=cost,
                request_body_json=request_body_json,
                prompt=prompt_final,
            )
            if ok:
                try:
                    from .stats_cache import invalidate_stats_cache

                    await invalidate_stats_cache(bot_id=bot_id or None)
                except Exception:  # noqa: BLE001
                    pass
                logger.info(
                    f"[RHComfyUI.Statistics] updated id={record_id} task={node.name} "
                    f"status={status} elapsed={elapsed_ms}ms"
                )
                return int(record_id)
            logger.warning(f"[RHComfyUI.Statistics] update id={record_id} 未找到行,回落 INSERT status={status}")

        # INSERT 终态(旧路径 / begin 失败 / update 未命中)
        kwargs = _build_common_insert_kwargs(
            request=request,
            node=node,
            bot_id=bot_id,
            group_id=group_id,
            trace_id=trace_id or (request.trace_id or ""),
            entry_point=entry_point,
            backend_key_prefix=backend_key_prefix,
            request_body=wire_body if isinstance(wire_body, dict) else request_body,
            status=status,
            elapsed_ms=elapsed_ms,
            point_cost=cost,
            error=error,
            raw_json=raw_json,
            saved_files_json=saved_files_json,
        )
        # 覆盖为最终 wire prompt / body( _build 仍可能用入参 )
        kwargs["prompt"] = prompt_final
        kwargs["request_body_json"] = request_body_json
        new_id: int = await RHComfyuiTaskRecord.insert_task_record(**kwargs)
        try:
            from .stats_cache import invalidate_stats_cache

            await invalidate_stats_cache(bot_id=bot_id or kwargs.get("bot_id") or None)
        except Exception:  # noqa: BLE001
            pass
        logger.info(
            f"[RHComfyUI.Statistics] recorded id={new_id} task={node.name} "
            f"user={kwargs['user_id']} status={status} elapsed={elapsed_ms}ms"
        )
        return new_id

    except Exception as e:  # noqa: BLE001 - 统计失败不影响主流程
        logger.warning(f"[RHComfyUI.Statistics] 记录失败(已忽略): {e}")
        return None


__all__ = ["begin_task", "record_task"]
