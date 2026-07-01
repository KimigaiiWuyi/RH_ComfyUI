"""任务统计写入 — 模块函数,无 DI、无单例

execute_generation() 的 finally 块直接 import 调用 record_task();
落库细节由 `RHComfyuiTaskRecord.insert_task_record` 负责
(classmethod + @with_session,统一 session 管理 + 重试 + commit)。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime, timezone

from gsuid_core.logger import logger

from ..core.request import TaskType

if TYPE_CHECKING:
    from ..core.request import GenerationResult, GenerationRequest
    from ..core.pipeline import NodeDef

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


# ── 核心入口 ──


async def record_task(
    *,
    request: "GenerationRequest",
    result: Optional["GenerationResult"],
    node: "NodeDef",
    status: str,
    elapsed_ms: int,
    error: Optional[str] = None,
    bot_id: str = "",
    group_id: str = "",
    trace_id: str = "",
) -> Optional[int]:
    """记录一次任务执行(模块函数,从 executor.py 直接调用)。

    整体 try/except 兜底:写库失败仅 logger.warning,不影响主流程。
    """
    try:
        # 1. 提取核心输入参数
        core = _extract_core_params(request)

        # 2. 序列化原始厂商响应(失败时 result 为 None,跳过)
        raw_json = ""
        if result is not None and result.raw:
            raw_json = _safe_json_dumps(result.raw, RAW_RESPONSE_MAX_BYTES)

        # 3. 序列化 extra params(用户 params + extra 合并)
        merged_params: dict[str, Any] = {}
        if request.params:
            merged_params.update(request.params)
        if request.extra:
            merged_params.update(request.extra)
        extra_json = _safe_json_dumps(merged_params, EXTRA_PARAMS_MAX_BYTES) if merged_params else ""

        # 4. 错误信息截断
        error_msg = _truncate_str(error or "", ERROR_MESSAGE_MAX_CHARS)

        # 5. backend_provider: 优先 node.provider
        provider = node.provider or ""

        # 6. backend_model: 多供应商时取 backend_models[provider],回退 backend_model
        model_id = node.backend_model or ""
        if provider and node.backend_models:
            selected = node.backend_models.get(provider)
            if selected:
                model_id = selected

        # 6.5) prompt:从 GenerationRequest.prompt 透传,截断到 4000 字符
        #   (与模型字段 max_length=4000 对齐;语音/音乐通常 < 200,视频图描述可上千字)
        prompt_str: str = _truncate_str(request.prompt or "", 4000)

        # 7. 调 RHComfyuiTaskRecord.insert_task_record 落库
        #    局部 import 避免循环引用(models 已 import statistics 链路,或反之)
        from .models import RHComfyuiTaskRecord

        # user_id: 空字符串 / None 都视为 "unknown",与旧行为一致
        user_id_raw = request.user_id
        resolved_user_id: str = user_id_raw if user_id_raw else "unknown"
        record_id: int = await RHComfyuiTaskRecord.insert_task_record(
            user_id=resolved_user_id[:64],
            bot_id=bot_id[:64] if bot_id else "",
            group_id=group_id[:64] if group_id else "",
            task_type=request.task_type.value,
            task_name=node.name[:128],
            backend=node.backend[:32] if node.backend else "",
            backend_model=model_id[:128],
            backend_provider=provider[:32],
            duration_seconds=core.get("duration_seconds"),
            width=core.get("width"),
            height=core.get("height"),
            ratio=str(core.get("ratio", ""))[:16],
            resolution=str(core.get("resolution", ""))[:16],
            seed=core.get("seed"),
            voice_id=str(core.get("voice_id", ""))[:64],
            extra_params_json=extra_json,
            prompt=prompt_str,
            status=status[:16],
            elapsed_ms=elapsed_ms,
            point_cost=int(node.point_cost or 0),
            error_message=error_msg,
            raw_response_json=raw_json,
            trace_id=trace_id[:64] if trace_id else "",
            created_at=datetime.now(timezone.utc),
        )

        logger.info(
            f"[RHComfyUI.Statistics] recorded id={record_id} task={node.name} "
            f"user={user_id_raw} status={status} elapsed={elapsed_ms}ms"
        )
        return record_id

    except Exception as e:
        # 兜底:统计失败不影响主流程
        logger.warning(f"[RHComfyUI.Statistics] 记录失败(已忽略): {e}")
        return None


__all__ = ["record_task"]
