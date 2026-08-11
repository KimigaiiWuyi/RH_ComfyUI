"""模型清单核心 API — 聚合 PipelineRegistry + AdapterRegistry

数据来源:
- `pipeline_registry.all_pipelines()` — 节点定义(编程式 defs.py)
- `model_registry` — ABC 模型实例;可用性 = 任一通道可用
  (含外部插件经 channel_registry 注入的通道,如网关);无 ABC 模型的历史
  节点才回退按 `backend_registry` 的 Adapter 判定

设计要点:
1. 复用 router 模块的 `_is_available`,但避免循环依赖,这里直接调用
2. 启动时缓存的模型清单在 bot 重启前不变;check_available 是网络配置
   读取,本身廉价,每次查询都跑一次保证反映最新状态
3. `include_unavailable=True` 时即使后端没配也列出来,方便排查
"""

from __future__ import annotations

from typing import Any, Optional
from dataclasses import field, asdict, dataclass

from gsuid_core.logger import logger

from ..utils.backends import backend_registry
from ..utils.core.types import MediaRef, MediaKind
from ..utils.core.request import CATALOG_GROUP_DISPLAY, TaskType
from ..utils.core.pipeline import NodeDef, pipeline_registry

# ═══════════════════════════════════════════════════════════════════════
#  TaskType / catalog_group 别名 — 接受中文友好查询
# ═══════════════════════════════════════════════════════════════════════

_TASK_ALIAS: dict[str, str] = {
    "image": "image",
    "img": "image",
    "图片": "image",
    "生图": "image",
    "视频": "video",
    "video": "video",
    "音乐": "music",
    "music": "music",
    "语音": "speech",
    "speech": "speech",
    "tts": "speech",
    # 目录分组:专用工具(非通用生成主列表)
    "tool": "tool",
    "misc": "tool",
    "杂项": "tool",
    "杂项工具": "tool",
}


def _resolve_task_type(raw: str | None) -> Optional[TaskType]:
    """把用户输入(中/英/缩写)规范化为 TaskType"""
    if not raw:
        return None
    key = raw.strip().lower()
    key = _TASK_ALIAS.get(key, key)
    try:
        return TaskType(key)
    except ValueError:
        return None


def _resolve_catalog_group(raw: str | None) -> Optional[str]:
    """规范化目录分组键(含 tool 等非 TaskType 分组)。"""
    if not raw:
        return None
    key = raw.strip().lower()
    key = _TASK_ALIAS.get(key, key)
    if key in CATALOG_GROUP_DISPLAY:
        return key
    # 兼容未知但合法的自定义分组字符串
    if key and key.isidentifier():
        return key
    return None


def _node_catalog_group(node: NodeDef) -> str:
    """NodeDef 的目录分组:显式 catalog_group 优先,否则 task_type。"""
    if node.catalog_group:
        return str(node.catalog_group)
    return node.task_type.value


# ═══════════════════════════════════════════════════════════════════════
#  ModelEntry — 序列化的模型条目
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ModelEntry:
    """单个模型对外暴露的视图"""

    name: str
    display_name: str
    task_type: str
    backend: str
    backend_model: Optional[str] = None
    description: str = ""
    point_cost: int = 0
    priority: int = 0
    available: bool = False
    unavailable_reason: Optional[str] = None
    supported_tasks: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    requirements: list[str] = field(default_factory=list)
    # ── 2026-07-02 ABC 重构新增(只增不改,调用方不读则行为不变) ──
    card: dict[str, Any] = field(default_factory=dict)
    channels: list[dict[str, Any]] = field(default_factory=list)
    execution_mode: str = "sync"
    # ── 输入能力显式标注:纯文生图模型 accepts_images=False,调用方/agent 不必解析
    #    input_schema 即可判定是否可传参考图,以及最多几张 ──
    accepts_images: bool = False
    max_input_images: int = 0
    # ── 2026-07-21 积分范围新增:前端展示"最低~最高积分"用 ──
    point_range: dict[str, int] = field(default_factory=lambda: {"min": 0, "max": 0})
    # ── 目录分组:默认等于 task_type;专用工具为 "tool"(不进图片生成主列表) ──
    catalog_group: str = ""
    # ── 2026-08 取消能力:进行中任务可 cancel;remote=上游 DELETE(通道级) ──
    # 缺省 False:未知/不完整条目 fail-closed,避免误展示取消
    supports_cancel: bool = False
    supports_remote_cancel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelEntry":
        card_raw = data.get("card")
        channels_raw = data.get("channels")
        point_range_raw = data.get("point_range")
        return cls(
            name=str(data.get("name", "")),
            display_name=str(data.get("display_name", "")),
            task_type=str(data.get("task_type", "")),
            backend=str(data.get("backend", "")),
            backend_model=data.get("backend_model"),
            description=str(data.get("description", "")),
            point_cost=int(data.get("point_cost", 0)),
            priority=int(data.get("priority", 0)),
            available=bool(data.get("available", False)),
            unavailable_reason=data.get("unavailable_reason"),
            supported_tasks=list(data.get("supported_tasks", [])),
            input_schema=dict(data.get("input_schema", {}) or {}),
            output_schema=dict(data.get("output_schema", {}) or {}),
            requirements=list(data.get("requirements", []) or []),
            card=dict(card_raw) if isinstance(card_raw, dict) else {},
            channels=list(channels_raw) if isinstance(channels_raw, list) else [],
            execution_mode=str(data.get("execution_mode") or "sync"),
            accepts_images=bool(data.get("accepts_images", False)),
            max_input_images=int(data.get("max_input_images", 0) or 0),
            point_range=(
                dict(point_range_raw)
                if isinstance(point_range_raw, dict)
                else {"min": 0, "max": 0}
            ),
            catalog_group=str(data.get("catalog_group") or data.get("task_type") or ""),
            supports_cancel=bool(data.get("supports_cancel", False)),
            supports_remote_cancel=bool(data.get("supports_remote_cancel", False)),
        )


# ═══════════════════════════════════════════════════════════════════════
#  聚合逻辑
# ═══════════════════════════════════════════════════════════════════════


def _model_node_backend(model_obj: Any) -> str:
    """读取模型 node.backend(无 node 时为空串)。"""
    try:
        node = model_obj.node
    except AttributeError:
        return ""
    if node is None:
        return ""
    try:
        backend = node.backend
    except AttributeError:
        return ""
    return str(backend or "").lower()


def _channel_supports_cancel(model_obj: Any, ch_name: str) -> bool:
    """通道级本地取消(cancel_generation)。rh_app 一律 False(只能 resume)。

    未知模型由调用方自行 fail-closed;此处假定 model_obj 已解析成功。
    """
    name = str(ch_name or "").strip().lower()
    if name == "rh_app" or name.startswith("rh_app"):
        return False
    if _model_node_backend(model_obj) == "rh_app":
        return False
    return bool(model_obj.supports_cancel)


def _aggregate_cancel_flags(
    channels: list[dict[str, Any]],
    *,
    fallback_cancel: bool,
    fallback_remote: bool,
) -> tuple[bool, bool]:
    """模型顶层 cancel 标志 = 各通道 OR,与 /models 通道明细一致(前端可只读顶层)。

    无通道时:local 回落 ``fallback_cancel``;remote 回落 ``fallback_remote``
    (remote 应为供应商级,无通道时通常 False)。多通道时顶层 true 表示
    「至少一路可 cancel」;精确到通道时前端应读 ``channels[].supports_*``。
    """
    if not channels:
        return fallback_cancel, fallback_remote
    any_cancel = any(bool(c["supports_cancel"]) for c in channels if "supports_cancel" in c)
    any_remote = any(bool(c["supports_remote_cancel"]) for c in channels if "supports_remote_cancel" in c)
    return any_cancel, any_remote


def _all_channels_support_cancel(channels: list[dict[str, Any]]) -> bool:
    """多通道未解析时:仅当每一路都 supports_cancel 才允许顶层取消。"""
    if not channels:
        return False
    flags: list[bool] = []
    for c in channels:
        if "supports_cancel" not in c:
            return False
        flags.append(bool(c["supports_cancel"]))
    return bool(flags) and all(flags)


def _channel_supports_remote_cancel(
    model_obj: Any,
    ch_name: str,
    *,
    channel: Any | None = None,
) -> bool:
    """供应商/通道级上游取消能力(**不读模型 ClassVar**)。

    优先 ``ProviderChannel.supports_remote_cancel()``;无实例时按通道名矩阵兜底。
    未知通道/供应商 → **False**(fail-closed)。

    **必须区分 rh_app 与 comfyui**(即便共用 RH_apikey):
    - ``rh_app``: RunningHub AI 应用,**无** cancel
    - ``comfyui`` / Comfy 工作流 ``runninghub``: 有 cancel/interrupt
    - Seedance ``ark``: DELETE;``runninghub`` Seedance 视频端点无 cancel
    """
    from ..core.channels.channel import ProviderChannel

    if isinstance(channel, ProviderChannel):
        return bool(channel.supports_remote_cancel())

    name = str(ch_name or "").strip().lower()
    if not name:
        return False

    backend = _model_node_backend(model_obj)
    if name == "rh_app" or name.startswith("rh_app") or backend == "rh_app":
        return False

    if name in ("comfyui", "comfyui-local"):
        return backend in ("", "comfyui")

    # runninghub 同名:Comfy 工作流可 cancel;Seedance 视频 API 不可
    if name == "runninghub":
        return backend == "comfyui"

    if name == "ark":
        return True

    if name in ("gemini", "gemini-image"):
        return True

    if name == "dashscope":
        return True

    # 聚合网关异步视频
    if name.startswith("gateway_slot") and (
        name.endswith("_seedance") or name.endswith("_happyhorse")
    ):
        return True

    # 聚合网关异步生图(gpt-image / banana);seedream 同步端 → False
    if name.startswith("gateway_slot"):
        if "seedream" in name:
            return False
        if "gpt_image" in name or "gemini" in name or "flash_image" in name or "banana" in name:
            return True
        return False

    return False


def _port_to_schema(port_dict: dict) -> dict[str, Any]:
    """把 NodeDef.inputs / outputs 序列化为 JSON 友好的 schema"""
    out: dict[str, Any] = {}
    for key, port in port_dict.items():
        try:
            out[key] = port.to_dict()
        except Exception:  # noqa: BLE001
            # 兜底:只暴露最小信息
            out[key] = {"type": str(getattr(port, "type", "unknown"))}
    return out


async def _build_entry(node) -> ModelEntry:  # noqa: ANN001
    """从 NodeDef 构建 ModelEntry;可用性以 ABC 模型为准(任一通道可用即可用)"""
    from ..core.routing.registry import model_registry

    available = False
    reason: Optional[str] = None
    card: dict[str, Any] = {}
    channels: list[dict[str, Any]] = []
    execution_mode = "sync"
    supports_cancel = False
    supports_remote_cancel = False

    model_obj = model_registry.get(node.name)
    if model_obj is not None:
        # 以模型的通道为准:内置 + 外部插件注入的通道(如网关/Azure)任一可用即可用。
        # 不能再按 backend Adapter 判定 —— Seedance/Gemini 已无 Adapter。
        try:
            available = await model_obj.check_available()
        except Exception as e:  # noqa: BLE001
            reason = f"check_available 异常: {e}"
        if not available and reason is None:
            try:
                reason = await model_obj.unavailable_reason()
            except Exception:  # noqa: BLE001
                reason = "无可用通道"
        card = model_obj.card.to_dict()
        execution_mode = model_obj.execution_mode
        supports_cancel = bool(model_obj.supports_cancel)
        for b in model_obj.channel_bindings():
            ch_name = b.channel.name
            ch_local = _channel_supports_cancel(model_obj, ch_name)
            ch_remote = _channel_supports_remote_cancel(
                model_obj, ch_name, channel=b.channel
            )
            channels.append(
                {
                    "name": ch_name,
                    "vendor_model": b.vendor_model,
                    "available": await b.channel.check_available(),
                    "supports_cancel": ch_local,
                    "supports_remote_cancel": ch_remote,
                }
            )
        # 顶层与通道明细一致(OR);remote 无通道时 False(供应商级,不读模型 ClassVar)
        supports_cancel, supports_remote_cancel = _aggregate_cancel_flags(
            channels,
            fallback_cancel=supports_cancel,
            fallback_remote=False,
        )
    else:
        # 回退:无 ABC 模型的历史节点仍按后端 Adapter 判定
        adapter = backend_registry.get(node.backend)
        if adapter is None:
            reason = f"后端 {node.backend} 未注册"
        else:
            try:
                available = await adapter.check_node_available(node)
            except Exception as e:  # noqa: BLE001
                reason = f"check_node_available 异常: {e}"
            if not available and reason is None:
                try:
                    reason = await adapter.get_node_unavailable_reason(node)
                except Exception:  # noqa: BLE001
                    reason = "后端不可用"
        # 无 ABC 时按 backend 名给保守 cancel 能力(与 _channel_* 规则对齐)
        if node.backend == "rh_app":
            supports_cancel = False
            supports_remote_cancel = False
        elif node.backend in ("comfyui", "gemini-image"):
            supports_cancel = True
            supports_remote_cancel = True

    # 输入能力:是否可传参考图 + 上限(纯文生图 / 纯文本模型无 images 端口)
    img_port = node.inputs.get("images") or node.inputs.get("image")
    accepts_images = img_port is not None
    max_input_images = int(img_port.max_items) if img_port is not None and img_port.max_items else 0

    # 积分范围:min ~ max(动态计费模型有范围,固定计费模型 min=max)
    range_min = range_max = node.point_cost
    if model_obj is not None:
        try:
            range_min, range_max = model_obj.point_range()
        except Exception:  # noqa: BLE001
            pass

    return ModelEntry(
        name=node.name,
        display_name=node.display_name,
        task_type=node.task_type.value,
        backend=node.backend,
        backend_model=node.backend_model,
        description=node.description,
        point_cost=node.point_cost,
        priority=node.capabilities.priority,
        available=available,
        unavailable_reason=None if available else reason,
        supported_tasks=list(node.capabilities.supported_tasks),
        input_schema=_port_to_schema(node.inputs),
        output_schema=_port_to_schema(node.outputs),
        requirements=list(node.requirements),
        card=card,
        channels=channels,
        execution_mode=execution_mode,
        accepts_images=accepts_images,
        max_input_images=max_input_images,
        point_range={"min": range_min, "max": range_max},
        catalog_group=_node_catalog_group(node),
        supports_cancel=supports_cancel,
        supports_remote_cancel=supports_remote_cancel,
    )


async def _build_entry_from_model(model) -> ModelEntry:  # noqa: ANN001
    """为无 NodeDef 的纯编程式模型(路径 C / 闭源 ABC 模型)构建 ModelEntry。

    这类模型只存在于 model_registry(model.node is None),不经 pipeline_registry;
    没有它们清单就违背了"注册即三入口可见"的承诺。backend 留空(无 Adapter)。
    """
    available = False
    reason: Optional[str] = None
    try:
        available = await model.check_available()
    except Exception as e:  # noqa: BLE001
        reason = f"check_available 异常: {e}"
    if not available and reason is None:
        try:
            reason = await model.unavailable_reason()
        except Exception:  # noqa: BLE001
            reason = "无可用通道"

    channels: list[dict[str, Any]] = []
    bindings = []
    try:
        bindings = model.channel_bindings()
    except Exception:  # noqa: BLE001
        pass
    for b in bindings:
        ch_name = b.channel.name
        ch_local = _channel_supports_cancel(model, ch_name)
        ch_remote = _channel_supports_remote_cancel(model, ch_name, channel=b.channel)
        channels.append(
            {
                "name": ch_name,
                "vendor_model": b.vendor_model,
                "available": await b.channel.check_available(),
                "supports_cancel": ch_local,
                "supports_remote_cancel": ch_remote,
            }
        )

    supports_cancel, supports_remote_cancel = _aggregate_cancel_flags(
        channels,
        fallback_cancel=bool(model.supports_cancel),
        fallback_remote=False,
    )

    input_schema = model.input_schema()
    img_port = input_schema.get("images") or input_schema.get("image")
    accepts_images = img_port is not None
    max_input_images = int(img_port.max_items) if img_port is not None and img_port.max_items else 0

    # 积分范围
    range_min = range_max = model.point_cost
    try:
        range_min, range_max = model.point_range()
    except Exception:  # noqa: BLE001
        pass

    modality = model.modality.value
    return ModelEntry(
        name=model.name,
        display_name=model.display_name,
        task_type=modality,
        backend="",
        description=model.card.description,
        point_cost=model.point_cost,
        priority=model.priority,
        available=available,
        unavailable_reason=None if available else reason,
        supported_tasks=[modality],
        input_schema=_port_to_schema(input_schema),
        output_schema=_port_to_schema(model.output_schema()),
        card=model.card.to_dict(),
        channels=channels,
        execution_mode=model.execution_mode,
        accepts_images=accepts_images,
        max_input_images=max_input_images,
        point_range={"min": range_min, "max": range_max},
        catalog_group=modality,
        supports_cancel=supports_cancel,
        supports_remote_cancel=supports_remote_cancel,
    )


def _deduplicate_by_name(entries: list[ModelEntry]) -> list[ModelEntry]:
    """按 name 分组去重,只保留 priority 最高的那一个。

    解决运行时路径存在重复 YAML 副本(内置 + 运行时两份)导致调用方看到重复模型的问题。
    同一 name 视为同一节点(可能是不同路径被加载两次),只保留 priority 最高者;
    不同 name 的节点即使 backend_model 相同也保留(如 qwen_2512 与 qwen_2512_alt),
    避免误伤本应独立展示的多入口业务节点。
    """
    best: dict[str, ModelEntry] = {}
    for entry in entries:
        if entry.name not in best or entry.priority > best[entry.name].priority:
            best[entry.name] = entry

    result: list[ModelEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.name in seen:
            continue  # 已被该 name 的优胜者保留,跳过
        if best[entry.name] is entry:
            result.append(entry)
            seen.add(entry.name)
        # 其余同名节点静默丢弃
    return result


async def build_model_catalog(
    *,
    include_unavailable: bool = True,
    task_type: Optional[str] = None,
    as_text: bool = False,
) -> dict[str, Any]:
    """构建完整模型清单

    Args:
        include_unavailable: 是否包含不可用的节点(排查/UI 用 True,AI 用 False)
        task_type: 任务类型过滤,支持中英文别名(如 "image" / "图片")
        as_text: 若为 True,在结果 dict 里附带 `text` 字段(LLM 友好)

    Returns:
        统一结构 dict,调用方可渲染、AI 可消费
    """
    # 懒加载兜底:启动钩子未跑完时,补一次加载
    if not pipeline_registry.all_pipelines():
        from ..utils.core.router import _ensure_runtime_initialized

        _ensure_runtime_initialized(pipeline_registry)

    # 过滤键:优先按 catalog_group(含 tool);兼容旧调用按 TaskType
    target_group = _resolve_catalog_group(task_type)
    nodes = pipeline_registry.all_pipelines()
    if target_group is not None:
        nodes = [n for n in nodes if _node_catalog_group(n) == target_group]

    entries: list[ModelEntry] = []
    for n in nodes:
        entry = await _build_entry(n)
        if not include_unavailable and not entry.available:
            continue
        entries.append(entry)

    # 纯编程式模型(model.node is None,如闭源 ABC 模型)不经 pipeline_registry,
    # 单独从 model_registry 补进清单,保证"注册即三入口可见"
    from ..core.routing.registry import model_registry

    node_names = {n.name for n in nodes}
    for model in model_registry.all_models():
        if model.node is not None or model.name in node_names:
            continue
        modality = model.modality.value
        if target_group is not None and modality != target_group:
            continue
        entry = await _build_entry_from_model(model)
        if not include_unavailable and not entry.available:
            continue
        entries.append(entry)

    # 去重:同一 name 只保留 priority 最高的节点
    # 解决运行时路径存在重复 YAML 副本导致调用方看到重复模型的问题
    entries = _deduplicate_by_name(entries)

    # 按目录分组排序,内部按 priority 倒序
    entries.sort(key=lambda e: (e.catalog_group or e.task_type, -e.priority, e.display_name))

    task_display = dict(CATALOG_GROUP_DISPLAY)
    # 保证实际出现的分组都有显示名
    for e in entries:
        g = e.catalog_group or e.task_type
        task_display.setdefault(g, g)

    result: dict[str, Any] = {
        # 对外 task_types = 目录分组键(含 tool);执行模态仍在每条 model.task_type
        "task_types": sorted({(e.catalog_group or e.task_type) for e in entries}),
        "task_display": task_display,
        "total": len(entries),
        "available_count": sum(1 for e in entries if e.available),
        "models": [e.to_dict() for e in entries],
    }

    if as_text:
        result["text"] = _entries_to_text(entries, task_display, target_group)
    return result


def _entries_to_text(entries: list[ModelEntry], task_display: dict[str, str], task_filter: Optional[str]) -> str:
    """把 entries 格式化为 LLM 友好的文本"""
    if not entries:
        return "当前没有任何可用模型。"
    lines: list[str] = []
    if task_filter:
        lines.append(f"可用模型({task_display.get(task_filter, task_filter)}):")
    else:
        lines.append("可用模型清单:")
    grouped: dict[str, list[ModelEntry]] = {}
    for e in entries:
        grouped.setdefault(e.catalog_group or e.task_type, []).append(e)
    for task, items in grouped.items():
        lines.append(f"\n[{task_display.get(task, task)}]")
        for e in items:
            cost = f", {e.point_cost} 积分" if e.point_cost else ""
            lines.append(f"- {e.display_name} (id={e.name}, backend={e.backend}{cost})")
    return "\n".join(lines)


async def get_models_by_task(task_type: str, *, include_unavailable: bool = True) -> dict[str, Any]:
    """按目录分组过滤 — FastAPI 路由 /api/RH_ComfyUI/models/{task_type} 使用

    参数名仍叫 task_type(兼容旧客户端),语义为 catalog_group:
    image / video / music / speech / tool …
    """
    target_group = _resolve_catalog_group(task_type)
    if target_group is None:
        return {
            "error": f"未知任务类型: {task_type!r}",
            "valid_types": sorted(CATALOG_GROUP_DISPLAY.keys()),
        }
    return await build_model_catalog(include_unavailable=include_unavailable, task_type=target_group)


async def build_backend_summary() -> dict[str, Any]:
    """后端可用性摘要 — 总览面板用"""
    backends_info: list[dict[str, Any]] = []
    for adapter in backend_registry.all():
        try:
            available = await adapter.check_available()
        except Exception:  # noqa: BLE001
            available = False
        reason: Optional[str] = None
        if not available:
            try:
                reason = await adapter.get_unavailable_reason()
            except Exception:  # noqa: BLE001
                reason = "不可用"
        models = [n for n in pipeline_registry.all_pipelines() if n.backend == adapter.name]
        backends_info.append(
            {
                "name": adapter.name,
                "available": available,
                "unavailable_reason": reason,
                "model_count": len(models),
            }
        )
    # 模型可用性以 ABC 模型的通道为准(Seedance/Gemini 无 Adapter,
    # 按 backend 判定会把它们永远算成不可用);无 ABC 模型的历史节点才回退按后端
    from ..core.routing.registry import model_registry

    nodes = pipeline_registry.all_pipelines()
    node_names = {n.name for n in nodes}
    pure_models = [m for m in model_registry.all_models() if m.node is None and m.name not in node_names]

    available_models = 0
    for n in nodes:
        model_obj = model_registry.get(n.name)
        if model_obj is not None:
            try:
                if await model_obj.check_available():
                    available_models += 1
            except Exception:  # noqa: BLE001
                pass
        elif any(b["name"] == n.backend and b["available"] for b in backends_info):
            available_models += 1
    for m in pure_models:
        try:
            if await m.check_available():
                available_models += 1
        except Exception:  # noqa: BLE001
            pass

    return {
        "backends": backends_info,
        "totals": {
            "backends": len(backends_info),
            "models": len(nodes) + len(pure_models),
            "available_models": available_models,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
#  动态积分估算
# ═══════════════════════════════════════════════════════════════════════


async def estimate_model_points(
    model_name: str,
    *,
    ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    quality: Optional[str] = None,
    resolution: Optional[str] = None,
    duration: Optional[int] = None,
    generate_audio: Optional[bool] = None,
    num_input_images: int = 0,
    num_video_refs: int = 0,
    input_video_duration: Optional[float] = None,
) -> dict[str, Any]:
    """根据用户实时选择的参数,估算某模型消耗的积分。

    供前端在用户切换 ratio/image_size/quality/resolution/duration/已连输入数量时
    实时预览扣费,无需真正发起生成。对未覆盖 estimate_cost 的模型,返回其静态 point_cost。

    Args:
        ratio: 输出宽高比,如 "1:1" / "16:9" / "auto"。透传到 GenerationRequest.ratio
            (顶层字段)和 params['ratio'](双轨,因不同模型读法不一)。
        image_size: 图片分辨率档位,如 "1K" / "2K" / "4K"。塞 params['image_size']。
        quality: 生成质量档位,如 "low" / "medium" / "high"。塞 params['quality']。
        resolution: 视频分辨率,如 "480p" / "720p" / "1080p"。塞 params['resolution']。
        duration: 视频时长(秒)。塞 GenerationRequest.duration(顶层字段,
            Seedance 等视频模型从顶层读)和 params['duration'](双轨)。
        generate_audio: 是否生成同步音频(Seedance 1.5 Pro)。塞 params['generate_audio']。
        num_input_images: 已连输入图数量(0=文生图)。estimate_cost 只取 len(),
            不读图片内容,用占位 bytes 即可,避免下载/解析真实图片。
        num_video_refs: 已连输入视频参考数(0=文生视频)。同 num_input_images,
            用占位对象让 len() 命中。
        input_video_duration: 输入参考视频总时长(秒)。Seedance token 公式:
            (输入视频时长+输出时长)×宽×高×fps/1024;前端探测到后传入,
            未传时按「每段默认 5s × 段数」估算。

    Returns:
        {
            "model": str,
            "point_cost": int,        # 估算积分(动态或静态)
            "is_dynamic": bool,       # True=由 estimate_cost 动态算出;False=静态兜底
            "point_range": {          # 积分范围(min, max)
                "min": int,
                "max": int,
            },
            "params": {               # 实际参与计算的参数(归一化后)
                "ratio": str|null,
                "image_size": str|null,
                "quality": str|null,
                "resolution": str|null,
                "duration": int|null,
                "generate_audio": bool|null,
                "num_input_images": int,
                "num_video_refs": int,
                "input_video_duration": float|null,
            },
        }
    """
    from ..utils.core.request import TaskType, GenerationRequest
    from ..core.routing.registry import model_registry

    model_obj = model_registry.get(model_name)
    if model_obj is None:
        return {
            "model": model_name,
            "point_cost": 0,
            "is_dynamic": False,
            "error": f"模型 {model_name!r} 未注册",
            "params": _build_echo_params(
                ratio=ratio,
                image_size=image_size,
                quality=quality,
                resolution=resolution,
                duration=duration,
                generate_audio=generate_audio,
                num_input_images=num_input_images,
                num_video_refs=num_video_refs,
                input_video_duration=input_video_duration,
            ),
        }

    params: dict[str, Any] = {}
    if image_size is not None:
        params["image_size"] = image_size
    if quality is not None:
        params["quality"] = quality
    if resolution is not None:
        params["resolution"] = resolution
    if duration is not None:
        params["duration"] = duration
    if generate_audio is not None:
        params["generate_audio"] = generate_audio
    if input_video_duration is not None:
        params["input_video_duration"] = input_video_duration

    # 占位:estimate_cost 只调 len() 不读内容,避免下载真实媒体
    placeholder_images = [b""] * max(num_input_images, 0) if num_input_images > 0 else []
    placeholder_video_refs: list[MediaRef] = [
        MediaRef(kind=MediaKind.VIDEO, data=b"") for _ in range(max(num_video_refs, 0))
    ]

    # ratio/duration 顶层:Seedance 等从 request 顶层字段读
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="",
        ratio=ratio,
        resolution=resolution,
        duration=duration if duration is not None else 5,
        params=params,
        images=placeholder_images,
        video_refs=placeholder_video_refs,
    )

    try:
        cost = model_obj.estimate_cost(req)
    except Exception as e:  # noqa: BLE001 - 估算失败不阻断前端,回落静态值
        logger.warning(f"[estimate] {model_name} 动态估算失败({e}),回落静态 point_cost")
        return {
            "model": model_name,
            "point_cost": model_obj.point_cost,
            "is_dynamic": False,
            "error": f"动态估算失败: {e}",
            "params": _build_echo_params(
                ratio=ratio,
                image_size=image_size,
                quality=quality,
                resolution=resolution,
                duration=duration,
                generate_audio=generate_audio,
                num_input_images=num_input_images,
                num_video_refs=num_video_refs,
                input_video_duration=input_video_duration,
            ),
        }

    is_dynamic = cost != model_obj.point_cost

    # 获取积分范围
    try:
        range_min, range_max = model_obj.point_range()
    except Exception:  # noqa: BLE001
        range_min = range_max = model_obj.point_cost

    return {
        "model": model_name,
        "point_cost": cost,
        "is_dynamic": is_dynamic,
        "point_range": {"min": range_min, "max": range_max},
        "params": _build_echo_params(
            ratio=ratio,
            image_size=image_size,
            quality=quality,
            resolution=resolution,
            duration=duration,
            generate_audio=generate_audio,
            num_input_images=num_input_images,
            num_video_refs=num_video_refs,
            input_video_duration=input_video_duration,
        ),
    }


def _build_echo_params(
    *,
    ratio: Optional[str],
    image_size: Optional[str],
    quality: Optional[str],
    resolution: Optional[str],
    duration: Optional[int],
    generate_audio: Optional[bool],
    num_input_images: int,
    num_video_refs: int,
    input_video_duration: Optional[float] = None,
) -> dict[str, Any]:
    """构造 estimate 返回值里的 params echo 字典,所有路径共用避免漂移。"""
    return {
        "ratio": ratio,
        "image_size": image_size,
        "quality": quality,
        "resolution": resolution,
        "duration": duration,
        "generate_audio": generate_audio,
        "num_input_images": num_input_images,
        "num_video_refs": num_video_refs,
        "input_video_duration": input_video_duration,
    }


__all__ = [
    "ModelEntry",
    "build_model_catalog",
    "build_backend_summary",
    "get_models_by_task",
    "estimate_model_points",
]
