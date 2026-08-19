"""RH_ComfyUI 公开 Python API — 给外部插件(外部插件 等)调用。

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

from typing import TYPE_CHECKING, Any, Optional
from dataclasses import field, dataclass

# 避免在模块导入时触发 Pipeline 加载(由插件 __init__.py 的 on_core_start 钩子触发),
# 这里仅暴露类型与函数定义,内部使用延迟 import。
from .utils.core.types import ProgressEvent, ProgressCallback  # noqa: F401  - 透出给外部使用

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
    # 必须在 外部插件 等下游插件被加载前保证注册表已初始化
    # (由 RH_ComfyUI/__init__.py 的 @on_core_start 钩子负责)。
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
            raise ValueError(f"未知 task_type: {task_type!r},合法值: {valid}") from None
        if task_type != model_task_type:
            raise ValueError(f"模型 {model!r} 的 task_type={model_task_type} 与请求 {task_type!r} 不一致")
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

    # (视频参考图与 ordered_content 图片项的预处理已下沉到
    #  VideoGenerationBase.normalize(),dispatch → model.run() 内统一执行,
    #  三入口共享同一实现,入口层不再重复)

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
    if model_obj.node is not None:
        backend_name = model_obj.node.backend

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


def settle_model_cost(
    model: str,
    usage: Optional[dict[str, Any]] = None,
    *,
    request: Any = None,
    params: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """按供应商 usage 计算实扣积分(预扣后的后结算)。无法换算时返回 None。

    HTTP 调用方在外部预扣成功后,用本函数或 ``result.point_cost`` 做差额对齐,
    禁止再按返回值全额扣一次。
    """
    from .core.billing.settle import settle_model_cost as _settle

    return _settle(model, usage, request=request, params=params)


async def reconcile_seedance_usage_billing(
    *,
    model_names: Optional[list[str] | tuple[str, ...]] = None,
    apply: bool = False,
    adjust_wallet: bool = True,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    """按供应商原始 usage 回算 Seedance 2.x 历史积分(只做差额)。

    仅处理统计表成功且未退款、并能从 raw_response 解析出 token 的行。
    ``apply=False`` 预览;``adjust_wallet`` 控制是否改 RHBind。
    """
    from .core.billing.reconcile import reconcile_seedance_usage_billing as _rec

    return await _rec(
        model_names=model_names,
        apply=apply,
        adjust_wallet=adjust_wallet,
        limit=limit,
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
        backend = m.node.backend if m.node is not None else ""
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


def get_model_input_schema(model: str) -> dict[str, Any]:
    """返回模型的 input_schema(与 webapi 模型目录同源);模型不存在返回 {}。

    下游插件(如外部 Agent)用它判断模型能力——例如 input_schema 含
    ``frame_mode`` 字段即支持 Seedance 系的多参考(reference)语义。
    """
    from .rh_models.api import _port_to_schema
    from .utils.core.pipeline import pipeline_registry

    if not pipeline_registry.all_pipelines():
        from .utils.core.router import _ensure_runtime_initialized

        _ensure_runtime_initialized(pipeline_registry)

    for n in pipeline_registry.all_pipelines():
        if n.name == model:
            return _port_to_schema(n.inputs)
    return {}


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
        "audio_payload",
        "width",
        "height",
        "ratio",
        "resolution",
        "duration",
        "omni_reference_task_type",
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

    # video_refs / audio_refs 先就位,便于下面把误入 images 的视频/音频回流
    for list_field in ("video_refs", "audio_refs"):
        if list_field in req_kwargs and req_kwargs[list_field]:
            kind = MediaKind.VIDEO if list_field == "video_refs" else MediaKind.AUDIO
            req_kwargs[list_field] = [_to_media_ref(d, kind) for d in req_kwargs[list_field]]
        else:
            req_kwargs[list_field] = []

    # 处理 images: 支持 list[bytes] 和 list[dict] (dict 可能含 url,需异步下载)。
    # 关键:若 bytes/mime/扩展名实为视频/音频,不得继续走 image 通道 —— 否则
    # Seedance content[] 会以 type=image_url 发出,上游返回
    # "image format is not supported"(VID-4001)。
    from .utils.core.types import MediaRef, sniff_media_kind

    video_refs: list = list(req_kwargs["video_refs"])
    audio_refs: list = list(req_kwargs["audio_refs"])
    if "images" in req_kwargs and req_kwargs["images"]:
        images: list[bytes] = []
        for d in req_kwargs["images"]:
            if isinstance(d, dict):
                raw = await _decode_media_dict(d)
                kind = (
                    sniff_media_kind(
                        data=raw,
                        mime=d.get("mime_type"),
                        url=d.get("url"),
                        filename=d.get("filename"),
                    )
                    or MediaKind.IMAGE
                )
                if kind == MediaKind.VIDEO:
                    video_refs.append(
                        MediaRef(
                            kind=MediaKind.VIDEO,
                            data=raw,
                            role=d.get("role"),
                            mime_type=d.get("mime_type"),
                            filename=d.get("filename"),
                        )
                    )
                elif kind == MediaKind.AUDIO:
                    audio_refs.append(
                        MediaRef(
                            kind=MediaKind.AUDIO,
                            data=raw,
                            role=d.get("role"),
                            mime_type=d.get("mime_type"),
                            filename=d.get("filename"),
                        )
                    )
                else:
                    images.append(raw)
            else:
                kind = sniff_media_kind(data=d) or MediaKind.IMAGE
                if kind == MediaKind.VIDEO:
                    video_refs.append(MediaRef(kind=MediaKind.VIDEO, data=d))
                elif kind == MediaKind.AUDIO:
                    audio_refs.append(MediaRef(kind=MediaKind.AUDIO, data=d))
                else:
                    images.append(d)
        req_kwargs["images"] = images
    else:
        req_kwargs["images"] = []
    req_kwargs["video_refs"] = video_refs
    req_kwargs["audio_refs"] = audio_refs

    # ordered_content: list[dict] → list[ContentItem](内部会按 mime/字节纠正 type)
    if "ordered_content" in req_kwargs and req_kwargs["ordered_content"]:
        req_kwargs["ordered_content"] = [_to_content_item(d) for d in req_kwargs["ordered_content"]]

    # reference_audio: bytes / dict → bytes
    if "reference_audio" in req_kwargs and isinstance(req_kwargs["reference_audio"], dict):
        req_kwargs["reference_audio"] = await _decode_media_bytes(req_kwargs["reference_audio"])

    # audio_payload: ASR 输入;bytes / dict → bytes(同 reference_audio)
    if "audio_payload" in req_kwargs and isinstance(req_kwargs["audio_payload"], dict):
        req_kwargs["audio_payload"] = await _decode_media_bytes(req_kwargs["audio_payload"])

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
    """dict → ContentItem。

    前端/调用方约定 type 为 image_url|video_url|audio_url|text|draft_task。
    若 type 与真实媒体不符(常见:视频被标成 image_url),以 mime/文件头/扩展名纠正,
    保证下游 Seedance content[] 使用正确的 video_url / audio_url 键。
    """
    from .utils.core.types import MediaKind, ContentItem, ContentItemType

    t = d.get("type")
    if not t:
        raise ValueError("ordered_content 项缺少 type 字段")
    role = d.get("role")
    if t == "text":
        return ContentItem(type=ContentItemType.TEXT, text=d.get("text", ""), role=role)
    if t == "draft_task":
        return ContentItem(type=ContentItemType.DRAFT_TASK, draft_task_id=d.get("draft_task_id", ""))

    declared: MediaKind | None = None
    if t in ("image_url", "image"):
        declared = MediaKind.IMAGE
    elif t in ("video_url", "video"):
        declared = MediaKind.VIDEO
    elif t in ("audio_url", "audio"):
        declared = MediaKind.AUDIO
    else:
        raise ValueError(f"未知 ordered_content.type: {t!r}")

    media_payload = d.get("media") or {
        "url": d.get("url"),
        "role": role,
        "mime_type": d.get("mime_type"),
        "filename": d.get("filename"),
        "data_base64": d.get("data_base64"),
        "data": d.get("data"),
    }
    media = _to_media_ref(media_payload, declared)
    # MediaRef.__post_init__ 已按 sniff 纠正 kind;ContentItem.type 必须跟 kind 对齐
    type_map = {
        MediaKind.IMAGE: ContentItemType.IMAGE,
        MediaKind.VIDEO: ContentItemType.VIDEO,
        MediaKind.AUDIO: ContentItemType.AUDIO,
    }
    return ContentItem(type=type_map[media.kind], media=media, role=role)


async def _decode_media_dict(d: dict[str, Any]) -> bytes:
    """dict{url/data_base64/data} → bytes (异步,可能需下载远端媒体)。"""
    return await _decode_media_bytes(d)


def _preview_media_url(url: str, *, limit: int = 120) -> str:
    """日志用 URL 预览:截断 + 对 data: 去掉 base64 体。"""
    s = (url or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low.startswith("data:"):
        head, _, _rest = s.partition(",")
        return f"{head},<payload len={len(s) - len(head) - 1}>"
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _decode_data_uri(url: str) -> bytes:
    """解析 ``data:[<mime>][;base64],<payload>`` → raw bytes。"""
    import base64

    # data:[<mediatype>][;base64],<data>
    if "," not in url:
        raise ValueError("非法 data URI:缺少逗号分隔 payload")
    header, payload = url.split(",", 1)
    if ";base64" in header.lower():
        # 允许缺省 padding
        pad = (-len(payload)) % 4
        if pad:
            payload = payload + ("=" * pad)
        return base64.b64decode(payload)
    # 非 base64:百分号解码
    from urllib.parse import unquote_to_bytes

    return unquote_to_bytes(payload)


async def _decode_media_bytes(d: dict[str, Any]) -> bytes:
    """异步下载 url 或解码 data_base64 / data URI。

    约定(调用方应先把本站相对路径内联成 data_base64):
    - ``data_base64`` / ``data`` 优先;
    - ``url`` 仅接受 ``http(s)://`` 公网链,或 ``data:`` URI;
    - 相对路径 / ``asset://`` / 其它协议 → 明确 ValueError,不再丢给 httpx
      触发难读的 ``UnsupportedProtocol``。
    """
    import base64

    from gsuid_core.logger import logger

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
    if not isinstance(url, str):
        raise ValueError(f"媒体 url 类型非法: {type(url).__name__}")
    url = url.strip()
    if not url:
        raise ValueError("媒体引用 url 为空")

    low = url.lower()
    if low.startswith("data:"):
        try:
            raw = _decode_data_uri(url)
        except Exception as exc:  # noqa: BLE001 — 统一成 ValueError 给调用方
            logger.error(
                f"[RH_ComfyUI.api] data URI 解码失败: {_preview_media_url(url)} err={type(exc).__name__}: {exc}"
            )
            raise ValueError(f"非法 data URI: {exc}") from exc
        logger.debug(f"[RH_ComfyUI.api] 媒体 data URI 解码成功 size={len(raw)}")
        return raw

    if not low.startswith(("http://", "https://")):
        # 常见漏网:本站相对路径未内联 / asset:// 误入 images / 脏 URL
        scheme = url.split(":", 1)[0] if ":" in url and not url.startswith("/") else "(relative)"
        preview = _preview_media_url(url)
        logger.error(
            f"[RH_ComfyUI.api] 媒体 url 缺少 http(s) 协议,拒绝下载: "
            f"scheme={scheme!r} url={preview!r} "
            f"(应先由调用方内联为 data_base64,或改走 ordered_content 透传 asset://)"
        )
        raise ValueError(f"媒体 url 必须是 http(s) 链接或 data URI,收到不可下载的 url={preview!r}")

    # 异步下载:调用方在 async submit() 的上下文中,使用 AsyncClient
    # 避免同步 httpx.Client 阻塞事件循环。
    import httpx

    logger.debug(f"[RH_ComfyUI.api] 下载媒体 url={_preview_media_url(url, limit=200)}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


# ── 三重余额公开 API(外部宿主 / 插件预扣) ──────────────────────────


async def charge_points(user_id: str, bot_id: str, amount: int, *, vip_tier=None, reason: str = ""):
    """预扣三桶余额;不足抛 PointsDeniedError。"""
    from .core.billing.points_api import charge_points as _charge

    return await _charge(user_id, bot_id, amount, vip_tier=vip_tier, reason=reason)


async def refund_points(user_id: str, bot_id: str, amount: int, *, vip_tier=None, reason: str = ""):
    """退回三桶(封顶档位 cap)。"""
    from .core.billing.points_api import refund_points as _refund

    return await _refund(user_id, bot_id, amount, vip_tier=vip_tier, reason=reason)


async def get_quota_status(user_id: str, bot_id: str, *, vip_tier=None):
    """查询三桶余额与下次刷新时间。"""
    from .core.billing.points_api import get_quota_status as _status

    return await _status(user_id, bot_id, vip_tier=vip_tier)


async def force_refill_points(user_id: str, bot_id: str, *, vip_tier=None):
    from .core.billing.points_api import force_refill_points as _refill

    return await _refill(user_id, bot_id, vip_tier=vip_tier)


def get_all_tier_quotas():
    from .core.billing.points_api import get_all_tier_quotas as _tiers

    return _tiers()


async def cancel_generation(
    *,
    trace_id: Optional[str] = None,
    record_id: Optional[int] = None,
    reason: str = "user_cancel",
) -> dict[str, Any]:
    """取消一次进行中的生成任务。

    通过 ``trace_id``(与 submit 时传入的相同)或统计表 ``record_id`` 定位。
    会先尝试上游 DELETE(Seedance ark / HappyHorse 等已 bind 的任务),再
    cancel 本进程 asyncio.Task;dispatch 侧记 status=cancelled 并退款。

    Returns:
        dict: ok / found / cancelled_local / cancelled_remote / model / message ...
    """
    from .core.dispatch.active_tasks import cancel_generation as _cancel

    return await _cancel(trace_id=trace_id, record_id=record_id, reason=reason)


async def resume_poll(
    *,
    model: str,
    vendor_task_id: str,
    channel: str = "",
    backend: str = "",
    kind: str = "",
    trace_id: str = "",
    record_id: Optional[int] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> GenerationResult:
    """进程重启后按上游 task_id 继续轮询(不重新扣费、不重提 create)。

    见 ``core.dispatch.resume``。进程重启后在有 vendor_task_id 时调用。
    """
    from .core.dispatch.resume import resume_poll as _resume

    return await _resume(
        model=model,
        vendor_task_id=vendor_task_id,
        channel=channel,
        backend=backend,
        kind=kind,
        trace_id=trace_id,
        record_id=record_id,
        on_progress=on_progress,
    )


__all__ = [
    "GenerationResult",
    "ProgressEvent",
    "ProgressCallback",
    "submit",
    "cancel_generation",
    "resume_poll",
    "settle_model_cost",
    "reconcile_seedance_usage_billing",
    "get_point_cost",
    "list_models",
    "get_model_input_schema",
    "is_available",
    "charge_points",
    "refund_points",
    "get_quota_status",
    "force_refill_points",
    "get_all_tier_quotas",
]
