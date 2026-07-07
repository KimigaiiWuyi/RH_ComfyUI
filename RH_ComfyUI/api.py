"""RH_ComfyUI 公开 Python API — 给外部插件(canvas_backend 等)调用。

设计原则:
- 只暴露 Pipeline / Adapter 引擎能力,完全不涉及 HTTP / 鉴权 / 积分 / 限速。
- `submit()` 是异步函数(内部 await adapter.execute),调用方直接 ``await`` 即可。
- 返回 `GenerationResult` 数据类,统一数据形态便于调用方保存与序列化。

示例::

    from RH_ComfyUI.api import submit, get_point_cost, list_models

    cost = get_point_cost("qwen_2512")
    result = await submit(model="qwen_2512", prompt="赛博猫")
    raw_bytes = result.data
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional
from dataclasses import field, dataclass

# 避免在模块导入时触发 Pipeline 加载(由插件 __init__.py 的 on_core_start 钩子触发),
# 这里仅暴露类型与函数定义,内部使用延迟 import。
from .utils.core.types import ProgressEvent  # noqa: F401  - 透出给外部使用

if TYPE_CHECKING:
    from .utils.core.types import MediaRef, MediaKind, ContentItem

# ═══════════════════════════════════════════════════════════════════════
#  数据类
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class GenerationResult:
    """生成结果,由 RH_ComfyUI 返回给调用方。"""

    kind: str  # image / video / music / speech / text
    model: str  # 实际路由到的节点 name
    backend: str  # 使用的后端
    data: bytes  # 主产物二进制
    outputs: dict[str, bytes] = field(default_factory=dict)  # 附属产物(如 last_frame)
    point_cost: int = 0  # 消耗积分
    elapsed_ms: int = 0  # 耗时
    mime_type: str = ""  # 主产物 MIME
    usage: dict[str, Any] = field(default_factory=dict)  # 用量统计
    raw: Any = None  # 厂商原始响应
    metadata: dict[str, Any] = field(default_factory=dict)  # 落盘路径等


# 进度回调类型:可选
ProgressCallback = Callable[[ProgressEvent], None]


# ═══════════════════════════════════════════════════════════════════════
#  公开 API
# ═══════════════════════════════════════════════════════════════════════


async def submit(
    *,
    model: str,
    prompt: str,
    task_type: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    bot_id: Optional[str] = None,
    group_id: Optional[str] = None,
    **kwargs: Any,
) -> GenerationResult:
    """提交生成任务:同步阻塞到完成。

    Args:
        model: 节点 name(即 YAML 的 name 字段),如 "qwen_2512" / "seedance2"。
        prompt: 生成提示词。
        task_type: 可选,任务类型过滤(image / video / music / speech / text)。
                   若提供,会校验与节点声明的 task_type 是否一致。
        on_progress: 进度回调(仅视频等异步任务有效)。
        bot_id: 触发者 Bot 平台 ID(如 "qq" / "discord"),透传到
                `executor.execute_generation` 写入 `RHComfyuiTaskRecord.bot_id`。
                不传则记为 ""(老行为,与 bot 命令路径不兼容时会丢失统计维度)。
        group_id: 触发者群号(私聊为空),同上。
        **kwargs: 透传给 `execute_generation` 的动态参数
                  (images / video_refs / audio_refs / width / height /
                   ratio / resolution / duration / seed / ...)。

    Returns:
        GenerationResult: 主产物 + 附属产物 + 积分/用量信息。

    Raises:
        ValueError: 未知模型 / task_type 不匹配。
        RuntimeError: 后端 Adapter 未注册。
        Exception: Adapter 自身抛出的错误。
    """
    # 延迟导入:避免 RH_ComfyUI 导入阶段就触发整个模型注册链。
    # 必须在 canvas_backend 等下游插件被加载前保证注册表已初始化
    # (由 RH_ComfyUI/__init__.py 的 @on_core_start 钩子负责)。
    from .utils.core.types import MediaRef
    from .utils.core.request import TaskType
    from .core.routing.registry import model_registry

    # 1) 解析模型
    model_obj = model_registry.get(model)
    if model_obj is None:
        raise ValueError(f"未知模型: {model!r}")

    # 2) 校验 task_type
    model_task_type = model_obj.modality.value
    if task_type:
        # 先确保是合法 TaskType,非法立刻抛清晰错误(避免后续字符串比较误判)
        try:
            TaskType(task_type)
        except ValueError:
            valid = [t.value for t in TaskType]
            raise ValueError(
                f"未知 task_type: {task_type!r},合法值: {valid}"
            ) from None
        if task_type != model_task_type:
            raise ValueError(
                f"模型 {model!r} 的 task_type={model_task_type} 与请求 {task_type!r} 不一致"
            )
        final_task_type = task_type
    else:
        final_task_type = model_task_type

    # 3) 构造 GenerationRequest
    #    - 直接字段
    #    - 参考图/视频/音频 (MediaRef)
    #    - ordered_content (ContentItem 列表)
    request = await _build_request(
        task_type=final_task_type,
        prompt=prompt,
        kwargs=kwargs,
    )

    # 3.5) 视频生成任务:对传入图片做预处理(等比缩放最长边 ≤ 800px + EXIF 校正)
    if request.task_type == TaskType.VIDEO:
        from .utils.image_process import preprocess_for_video

        if request.images:
            request.images = [preprocess_for_video(img) for img in request.images]

        if request.ordered_content:
            from .utils.core.types import ContentItem as _CI, ContentItemType as _CIT

            for i, item in enumerate(request.ordered_content):
                if item.type == _CIT.IMAGE and item.media and item.media.data:
                    request.ordered_content[i] = _CI(
                        type=item.type,
                        media=MediaRef(
                            kind=item.media.kind,
                            data=preprocess_for_video(item.media.data),
                            url=item.media.url,
                            role=item.media.role,
                            mime_type=item.media.mime_type,
                            filename=item.media.filename,
                        ),
                        role=item.role,
                        text=item.text,
                    )

    # 4) 执行:统一调度器(路由/校验/限流/统计;计费为 ExternalPrepaidPolicy,
    #    即调用方已在外部记账,引擎侧只记账不扣费)
    from .core.billing.policy import BillingContext
    from .core.dispatch.context import DispatchContext
    from .core.dispatch.dispatcher import dispatch
    from .core.billing.external_policy import ExternalPrepaidPolicy

    request.model = model
    ctx = DispatchContext(
        billing=BillingContext(
            user_id=str(request.user_id or ""),
            bot_id=bot_id or "",
            entry_point="http",
        ),
        policy=ExternalPrepaidPolicy(),
        on_progress=on_progress,
        group_id=group_id or "",
        trace_id=request.trace_id or "",
    )
    result = await dispatch(request, ctx)

    # 5) 包装为对外的 GenerationResult
    outputs_bytes: dict[str, bytes] = {}
    for k, v in (result.outputs or {}).items():
        if isinstance(v, (bytes, bytearray)):
            outputs_bytes[k] = bytes(v)

    backend_name = str((result.metadata or {}).get("channel", ""))
    node = getattr(model_obj, "node", None)
    if node is not None:
        backend_name = node.backend

    return GenerationResult(
        kind=final_task_type,
        model=model_obj.name,
        backend=backend_name,
        data=result.data,
        outputs=outputs_bytes,
        point_cost=result.cost_points or model_obj.point_cost,
        elapsed_ms=int((result.metadata or {}).get("elapsed_ms", 0)),
        mime_type=result.mime_type,
        usage=result.usage or {},
        raw=result.raw or None,
        metadata=result.metadata or {},
    )


def get_point_cost(model: str) -> Optional[int]:
    """查询模型的积分消耗(不执行生成)。

    Args:
        model: 节点 name。

    Returns:
        积分值,模型不存在则返回 None。
    """
    from .core.routing.registry import model_registry

    m = model_registry.get(model)
    return m.point_cost if m else None


def list_models(task_type: Optional[str] = None) -> list[dict[str, Any]]:
    """列出可用模型。

    Args:
        task_type: 可选,过滤任务类型(image / video / music / speech / text)。

    Returns:
        每个模型一条 dict::
            {
                "name": "qwen_2512",
                "display_name": "千问 Qwen-Image 2512",
                "task_type": "image",
                "backend": "comfyui",
                "point_cost": 20,
            }
    """
    from .core.routing.registry import model_registry

    out: list[dict[str, Any]] = []
    for m in model_registry.all_models():
        if task_type and m.modality.value != task_type:
            continue
        node = getattr(m, "node", None)
        backend = node.backend if node is not None else ""
        out.append(
            {
                "name": m.name,
                "display_name": m.display_name,
                "task_type": m.modality.value,
                "backend": backend,
                "point_cost": m.point_cost,
                "description": m.card.description,
            }
        )
    return out


def is_available() -> bool:
    """RH_ComfyUI 引擎是否可用(已注册至少 1 个模型)。"""
    from .core.routing.registry import model_registry

    return len(model_registry.all_models()) > 0


# ═══════════════════════════════════════════════════════════════════════
#  内部:GenerationRequest 构造
# ═══════════════════════════════════════════════════════════════════════


async def _build_request(*, task_type: str, prompt: str, kwargs: dict[str, Any]) -> Any:
    """根据 kwargs 构造 GenerationRequest (异步,可能需下载远端媒体)。"""
    from .utils.core.types import (
        MediaKind,
    )
    from .utils.core.request import TaskType, GenerationRequest

    # 拆出"我们处理的字段"和"透传到 params 的字段"
    handled = {
        "negative_prompt",
        "images",
        "video_refs",
        "audio_refs",
        "ordered_content",
        "reference_audio",
        "width",
        "height",
        "ratio",
        "resolution",
        "duration",
        "seed",
        "generate_audio",
        "watermark",
        "camera_fixed",
        "return_last_frame",
        "service_tier",
        "voice_id",
        "mood",
        "speed",
        "language_boost",
        "model",
        "params",
        # 上下文:由调用方注入,落到 GenerationRequest.user_id / trace_id,
        # 最终在 statistics.record_task() 写入 RHComfyuiTaskRecord.user_id。
        # 之前漏写时会被吞到 passthrough → params,导致 record_task 拿不到 user_id
        # 全部回落到 "unknown",这是 #73 之前的 bug 根因。
        "user_id",
        "trace_id",
    }

    req_kwargs: dict[str, Any] = {"task_type": TaskType(task_type), "prompt": prompt}
    passthrough: dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in handled:
            req_kwargs[k] = v
        else:
            passthrough[k] = v

    # 处理 images: 支持 list[bytes] 和 list[dict] (dict 可能含 url,需异步下载)
    if "images" in req_kwargs and req_kwargs["images"]:
        images: list[bytes] = []
        for d in req_kwargs["images"]:
            if isinstance(d, dict):
                images.append(await _decode_media_dict(d))
            else:
                images.append(d)
        req_kwargs["images"] = images
    else:
        req_kwargs["images"] = []

    # video_refs / audio_refs: list[dict] → list[MediaRef]
    for list_field in ("video_refs", "audio_refs"):
        if list_field in req_kwargs and req_kwargs[list_field]:
            kind = MediaKind.VIDEO if list_field == "video_refs" else MediaKind.AUDIO
            req_kwargs[list_field] = [_to_media_ref(d, kind) for d in req_kwargs[list_field]]
        else:
            req_kwargs[list_field] = []

    # ordered_content: list[dict] → list[ContentItem]
    if "ordered_content" in req_kwargs and req_kwargs["ordered_content"]:
        req_kwargs["ordered_content"] = [_to_content_item(d) for d in req_kwargs["ordered_content"]]

    # reference_audio: bytes / dict → bytes
    if "reference_audio" in req_kwargs and isinstance(req_kwargs["reference_audio"], dict):
        req_kwargs["reference_audio"] = await _decode_media_bytes(req_kwargs["reference_audio"])

    # 透传后端私有参数到 params
    if passthrough:
        existing_params = req_kwargs.get("params") or {}
        if isinstance(existing_params, dict):
            existing_params.update(passthrough)
            req_kwargs["params"] = existing_params
        else:
            req_kwargs["params"] = passthrough

    return GenerationRequest(**req_kwargs)


def _to_media_ref(d: dict[str, Any], kind: MediaKind) -> MediaRef:
    """dict → MediaRef。"""
    from .utils.core.types import MediaRef

    data = d.get("data_base64") or d.get("data")
    url = d.get("url")
    if isinstance(data, str):
        import base64

        data = base64.b64decode(data)
    return MediaRef(
        kind=kind,
        data=data,
        url=url,
        role=d.get("role"),
        mime_type=d.get("mime_type"),
        filename=d.get("filename"),
    )


def _to_content_item(d: dict[str, Any]) -> ContentItem:
    """dict → ContentItem。"""
    from .utils.core.types import MediaKind, ContentItem, ContentItemType

    t = d.get("type")
    if not t:
        raise ValueError("ordered_content 项缺少 type 字段")
    role = d.get("role")
    if t == "text":
        return ContentItem(type=ContentItemType.TEXT, text=d.get("text", ""), role=role)
    if t in ("image_url", "image"):
        media = _to_media_ref(d.get("media") or {"url": d.get("url"), "role": role}, MediaKind.IMAGE)
        return ContentItem(type=ContentItemType.IMAGE, media=media, role=role)
    if t in ("video_url", "video"):
        media = _to_media_ref(d.get("media") or {"url": d.get("url"), "role": role}, MediaKind.VIDEO)
        return ContentItem(type=ContentItemType.VIDEO, media=media, role=role)
    if t in ("audio_url", "audio"):
        media = _to_media_ref(d.get("media") or {"url": d.get("url"), "role": role}, MediaKind.AUDIO)
        return ContentItem(type=ContentItemType.AUDIO, media=media, role=role)
    if t == "draft_task":
        return ContentItem(type=ContentItemType.DRAFT_TASK, draft_task_id=d.get("draft_task_id", ""))
    raise ValueError(f"未知 ordered_content.type: {t!r}")


async def _decode_media_dict(d: dict[str, Any]) -> bytes:
    """dict{url/data_base64/data} → bytes (异步,可能需下载远端媒体)。"""
    return await _decode_media_bytes(d)


async def _decode_media_bytes(d: dict[str, Any]) -> bytes:
    """异步下载 url 或解码 data_base64。"""
    import base64

    if d.get("data_base64"):
        return base64.b64decode(d["data_base64"])
    if d.get("data"):
        v = d["data"]
        if isinstance(v, str):
            return base64.b64decode(v)
        return v
    url = d.get("url")
    if not url:
        raise ValueError("媒体引用缺少 url/data/data_base64")
    # 异步下载:调用方在 async submit() 的上下文中,使用 AsyncClient
    # 避免同步 httpx.Client 阻塞事件循环。
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


__all__ = [
    "GenerationResult",
    "ProgressEvent",
    "ProgressCallback",
    "submit",
    "get_point_cost",
    "list_models",
    "is_available",
]
