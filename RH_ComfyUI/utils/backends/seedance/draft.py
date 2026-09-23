"""Seedance 2.5 样片两步的参数读取。

样片只出 480p;成片只带样片任务号出 1080p。成片复用的提示词、
素材、时长、宽高比等不能再写进上游 body。
"""

from __future__ import annotations

from ....utils.core.request import GenerationRequest

_DRAFT_TASK_ID_MAX = 128


def draft_task_id_from_params(params: object) -> str:
    """读 params.draft_task_id。缺省或非字符串视为没有成片任务号。"""
    if not isinstance(params, dict) or "draft_task_id" not in params:
        return ""
    raw = params["draft_task_id"]
    if isinstance(raw, str):
        return raw.strip()
    return ""


def draft_task_id_from_request(request: GenerationRequest) -> str:
    """params 优先,否则取 ordered_content 里的 draft_task 项。"""
    found = draft_task_id_from_params(request.params)
    if found:
        return found
    from ....utils.core.types import ContentItemType

    for item in request.ordered_content:
        if item.type != ContentItemType.DRAFT_TASK:
            continue
        raw = item.draft_task_id
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def is_draft_flag(params: object) -> bool:
    """params.draft 为真:布尔 True,或字符串 true。"""
    if not isinstance(params, dict) or "draft" not in params:
        return False
    raw = params["draft"]
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() == "true"
    return False


def is_valid_draft_task_id(task_id: str) -> bool:
    """方舟任务号只含字母、数字、连字符和下划线。"""
    if not task_id or len(task_id) > _DRAFT_TASK_ID_MAX:
        return False
    for ch in task_id:
        if not (ch.isalnum() or ch in "-_"):
            return False
    return True


_DRAFT_ROUND_TOKENS = frozenset({"draft", "样片"})
_FINAL_ROUND_TOKENS = frozenset({"final", "成片"})


def split_seedance25_round(model_name: str | None, prompt: str) -> tuple[str, str]:
    """模型名之后的第一个词决定轮次: draft/样片 或 final/成片。

    其它模型，以及没有这两个词的 seedance2.5，都是普通一轮生成。
    """
    if (model_name or "").strip() != "seedance2.5":
        return "normal", prompt
    parts = prompt.split(maxsplit=1)
    if not parts:
        return "normal", ""
    token = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if token in _DRAFT_ROUND_TOKENS:
        return "draft", rest
    if token in _FINAL_ROUND_TOKENS:
        # 「成片要电影感」不是任务号,按普通提示词生成
        task_token = rest.split(maxsplit=1)[0] if rest else ""
        if rest and len(rest.split()) == 1 and is_valid_draft_task_id(task_token):
            return "final", rest
        return "normal", prompt
    return "normal", prompt


def seedance_draft_user_text(vendor_task_id: str) -> str:
    """命令回复。样片必须带任务号,否则用户无法发起成片。"""
    task_id = vendor_task_id.strip()
    if not task_id:
        return "✅ 样片已生成。确认效果后可以再说出成片。"
    return f"✅ 样片已生成。确认后发送：生视频 seedance2.5 final {task_id}"


def seedance_ai_success_text(
    *,
    model_used: str,
    params: object,
    vendor_task_id: str,
    channel: str,
) -> str:
    """给 AI 的工具返回。样片必须带任务号，并写明要等用户下一轮确认。"""
    if is_draft_flag(params):
        return (
            f"样片已生成，使用模型: {model_used}。这是 480p 预览，不是成片。\n"
            f"vendor_task_id: {vendor_task_id}\n"
            f"channel: {channel}\n"
            "把样片交给用户看。用户没有在之后的对话里明确同意成片之前，"
            "不要再调用生视频。用户同意后，下一轮单独调用，"
            f'text 使用 "seedance2.5 final {vendor_task_id}"。'
        )
    if draft_task_id_from_params(params):
        return f"成片已生成，使用模型: {model_used}。这是 1080p 成片，样片流程结束。"
    return f"生成完成，使用模型: {model_used}"


def ordered_content_is_draft_only(request: GenerationRequest, draft_id: str) -> bool:
    """成片的 ordered_content 只能是这一条样片任务,不能再夹素材。"""
    from ....utils.core.types import ContentItemType

    for item in request.ordered_content:
        if item.type == ContentItemType.DRAFT_TASK and (item.draft_task_id or "").strip() == draft_id:
            continue
        return False
    return True


def apply_seedance25_round_resolution(request: GenerationRequest) -> None:
    """样片固定 480p,成片固定 1080p。调用方带了别的分辨率也改过来。"""
    if not isinstance(request.params, dict):
        return
    draft_id = draft_task_id_from_request(request)
    if draft_id and not draft_task_id_from_params(request.params):
        request.params["draft_task_id"] = draft_id
    if draft_id:
        request.resolution = "1080p"
        request.params["resolution"] = "1080p"
        return
    if is_draft_flag(request.params):
        request.resolution = "480p"
        request.params["resolution"] = "480p"


class Seedance25FinalCall:
    """成片提交前已经钉扎好的通道和计价字段。"""

    def __init__(
        self,
        task_id: str,
        channel: str,
        duration: int | None,
        input_video_duration: float | None,
    ) -> None:
        self.task_id = task_id
        self.channel = channel
        self.duration = duration
        self.input_video_duration = input_video_duration


def explicit_final_duration(params: object) -> int | None:
    """只有 params 里写了 duration 才算调用方指定。dataclass 默认 5 不算。"""
    if not isinstance(params, dict) or "duration" not in params:
        return None
    raw = params["duration"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw


def explicit_final_input_duration(params: object) -> float | None:
    if not isinstance(params, dict) or "input_video_duration" not in params:
        return None
    raw = params["input_video_duration"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


async def resolve_seedance25_final(
    draft_task_id: str,
    *,
    channel: str = "",
    duration: int | None = None,
    input_video_duration: float | None = None,
    user_id: str = "",
) -> Seedance25FinalCall:
    """校验样片任务号，并按消费记录补通道和计价时长。

    必须能找到本插件记下的成功样片。过期、失败、别人的任务号、通道不一致都拒绝。
    """
    from ....core.base.errors import ValidationError

    task_id = draft_task_id.strip()
    if not task_id:
        raise ValidationError("成片必须提供样片任务号 draft_task_id")
    if not is_valid_draft_task_id(task_id):
        raise ValidationError("样片任务号无效")

    from datetime import datetime, timezone, timedelta

    from ....utils.database.models import RHComfyuiTaskRecord

    origin = await RHComfyuiTaskRecord.find_draft_task_origin(task_id)
    if origin is None:
        raise ValidationError("找不到该样片的成功记录,不能生成成片")
    if origin.status != "ok":
        raise ValidationError("样片任务尚未成功,不能生成成片")
    created = origin.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(days=7):
        raise ValidationError("样片任务号已超过 7 天,不能再生成成片")
    owner = origin.user_id.strip()
    caller = user_id.strip()
    if owner and owner != "unknown" and caller != owner:
        raise ValidationError("成片必须由样片所属用户发起")
    pinned = channel.strip()
    if pinned == "auto":
        pinned = ""
    if pinned and origin.channel and pinned != origin.channel:
        raise ValidationError(f"成片通道必须与样片相同,样片通道为 {origin.channel}")
    if not pinned:
        pinned = origin.channel
    if not pinned:
        raise ValidationError("找不到该样片任务的通道,请传入 channel")
    resolved_duration = duration
    if resolved_duration is None and isinstance(origin.duration_seconds, int) and origin.duration_seconds != 0:
        resolved_duration = origin.duration_seconds
    resolved_input = input_video_duration
    if resolved_input is None and origin.input_video_duration is not None:
        resolved_input = origin.input_video_duration
    return Seedance25FinalCall(task_id, pinned, resolved_duration, resolved_input)


async def prepare_seedance25_final_request(request: GenerationRequest, *, user_id: str) -> None:
    """dispatch 入口:成片在校验和预扣之前钉扎通道、分辨率和计价时长。"""
    if (request.model or "").strip() != "seedance2.5":
        return
    draft_id = draft_task_id_from_request(request)
    if not draft_id:
        return
    if isinstance(request.params, dict) and not draft_task_id_from_params(request.params):
        request.params["draft_task_id"] = draft_id
    channel = request.channel if isinstance(request.channel, str) else ""
    planned = await resolve_seedance25_final(
        draft_id,
        channel=channel,
        duration=explicit_final_duration(request.params),
        input_video_duration=explicit_final_input_duration(request.params),
        user_id=user_id,
    )
    request.channel = planned.channel
    request.resolution = "1080p"
    if isinstance(request.params, dict):
        request.params["resolution"] = "1080p"
        if planned.input_video_duration is not None:
            request.params["input_video_duration"] = planned.input_video_duration
    if planned.duration is not None:
        request.duration = planned.duration
