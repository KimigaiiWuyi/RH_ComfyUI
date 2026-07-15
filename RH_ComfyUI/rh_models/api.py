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

from ..utils.backends import backend_registry
from ..utils.core.request import TASK_DISPLAY_NAME, TaskType
from ..utils.core.pipeline import pipeline_registry

# ═══════════════════════════════════════════════════════════════════════
#  TaskType 别名 — 接受中文友好查询
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelEntry":
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
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            requirements=list(data.get("requirements", [])),
        )


# ═══════════════════════════════════════════════════════════════════════
#  聚合逻辑
# ═══════════════════════════════════════════════════════════════════════


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
        for b in model_obj.channel_bindings():
            channels.append(
                {
                    "name": b.channel.name,
                    "vendor_model": b.vendor_model,
                    "available": await b.channel.check_available(),
                }
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

    # 输入能力:是否可传参考图 + 上限(纯文生图 / 纯文本模型无 images 端口)
    img_port = node.inputs.get("images") or node.inputs.get("image")
    accepts_images = img_port is not None
    max_input_images = int(img_port.max_items) if img_port is not None and img_port.max_items else 0

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
        channels.append(
            {
                "name": b.channel.name,
                "vendor_model": b.vendor_model,
                "available": await b.channel.check_available(),
            }
        )

    input_schema = model.input_schema()
    img_port = input_schema.get("images") or input_schema.get("image")
    accepts_images = img_port is not None
    max_input_images = int(img_port.max_items) if img_port is not None and img_port.max_items else 0

    return ModelEntry(
        name=model.name,
        display_name=model.display_name,
        task_type=model.modality.value,
        backend="",
        description=model.card.description,
        point_cost=model.point_cost,
        priority=model.priority,
        available=available,
        unavailable_reason=None if available else reason,
        supported_tasks=[model.modality.value],
        input_schema=_port_to_schema(input_schema),
        output_schema=_port_to_schema(model.output_schema()),
        card=model.card.to_dict(),
        channels=channels,
        execution_mode=model.execution_mode,
        accepts_images=accepts_images,
        max_input_images=max_input_images,
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

    target_task = _resolve_task_type(task_type)
    nodes = pipeline_registry.all_pipelines()
    if target_task is not None:
        nodes = [n for n in nodes if n.task_type == target_task]

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
        if target_task is not None and model.modality != target_task:
            continue
        entry = await _build_entry_from_model(model)
        if not include_unavailable and not entry.available:
            continue
        entries.append(entry)

    # 去重:同一 name 只保留 priority 最高的节点
    # 解决运行时路径存在重复 YAML 副本导致调用方看到重复模型的问题
    entries = _deduplicate_by_name(entries)

    # 按 task_type 排序,内部按 priority 倒序
    entries.sort(key=lambda e: (e.task_type, -e.priority, e.display_name))

    task_display = {t.value: name for t, name in TASK_DISPLAY_NAME.items()}

    result: dict[str, Any] = {
        "task_types": sorted({e.task_type for e in entries}),
        "task_display": task_display,
        "total": len(entries),
        "available_count": sum(1 for e in entries if e.available),
        "models": [e.to_dict() for e in entries],
    }

    if as_text:
        result["text"] = _entries_to_text(entries, task_display, target_task.value if target_task else None)
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
        grouped.setdefault(e.task_type, []).append(e)
    for task, items in grouped.items():
        lines.append(f"\n[{task_display.get(task, task)}]")
        for e in items:
            cost = f", {e.point_cost} 积分" if e.point_cost else ""
            lines.append(f"- {e.display_name} (id={e.name}, backend={e.backend}{cost})")
    return "\n".join(lines)


async def get_models_by_task(task_type: str, *, include_unavailable: bool = True) -> dict[str, Any]:
    """按任务类型过滤 — FastAPI 路由 /api/RH_ComfyUI/models/{task_type} 使用"""
    target_task = _resolve_task_type(task_type)
    if target_task is None:
        return {
            "error": f"未知任务类型: {task_type!r}",
            "valid_types": [t.value for t in TaskType],
        }
    return await build_model_catalog(include_unavailable=include_unavailable, task_type=target_task.value)


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


__all__ = [
    "ModelEntry",
    "build_model_catalog",
    "build_backend_summary",
    "get_models_by_task",
]
