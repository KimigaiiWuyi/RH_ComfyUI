"""RH_ComfyUI 模型清单 — 工具函数模块.

集中放置:
- 文本格式化(把 ModelEntry 列表渲染为人类可读 / LLM 友好文本)
- AI 工具函数(ai_list_models)

这些函数被 `__init__.py` 中的命令处理器和 `webapi.py` 中的 FastAPI 路由共享,
放在 utils 里避免 `__init__.py` 膨胀。
"""

from __future__ import annotations

from .api import (
    ModelEntry,
    build_model_catalog,
)

# ═══════════════════════════════════════════════════════════════════════
#  文本格式化 — 人类可读
# ═══════════════════════════════════════════════════════════════════════


def format_text(catalog: dict[str, object], task_filter: str | None = None) -> str:
    """把目录数据格式化为人类可读文本

    Args:
        catalog: `build_model_catalog()` 的返回值
        task_filter: 当前过滤的任务标识(可选, 用于文案展示)

    Returns:
        适合直接 `bot.send()` 的多行字符串
    """
    models = catalog.get("models", [])
    raw_display = catalog.get("task_display")
    task_display: dict[str, str] = raw_display if isinstance(raw_display, dict) else {}
    if not isinstance(models, list):
        return "❌ 模型清单为空"
    if not models:
        scope = f"任务 {task_display.get(task_filter, task_filter)} " if task_filter else ""
        return f"❌ 当前{scope}没有任何可用模型"

    lines: list[str] = []
    if task_filter:
        lines.append(f"📦 RH_ComfyUI 可用模型 ({task_display.get(task_filter, task_filter)})")
    else:
        lines.append("📦 RH_ComfyUI 可用模型清单")
    lines.append("=" * 32)

    # 按目录分组(catalog_group; 缺省回退 task_type)
    grouped: dict[str, list[ModelEntry]] = {}
    for m in models:  # type: ignore[union-attr]
        if not isinstance(m, dict):
            continue
        entry = ModelEntry.from_dict(m)
        group_key = entry.catalog_group or entry.task_type
        grouped.setdefault(group_key, []).append(entry)

    for task, entries in grouped.items():
        lines.append(f"\n【{task_display.get(task, task)}】")
        for e in entries:
            status = "✅" if e.available else "❌"
            cost = f" {e.point_cost}积分" if e.point_cost else ""
            lines.append(f"  {status} {e.display_name} (`{e.name}`) — {e.backend}{cost}")
            if not e.available and e.unavailable_reason:
                lines.append(f"      ⚠ {e.unavailable_reason}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
#  AI 工具
# ═══════════════════════════════════════════════════════════════════════


async def ai_list_models(task_type: str = "") -> str:
    """AI 工具:列出当前所有可用模型

    返回结构化纯文本(已分任务类型、已剔除不可用项),
    LLM 据此决定向哪个 model 路由请求,避免把整张清单塞进 system prompt。

    Args:
        task_type: 可选过滤,接受 `image` / `video` / `music` / `speech`
                   及中文别名。留空返回全量。
    """
    catalog = await build_model_catalog(
        include_unavailable=False,
        task_type=task_type or None,
        as_text=True,
    )
    text = catalog.get("text")
    return text if isinstance(text, str) else str(catalog)


__all__ = [
    "format_text",
    "ai_list_models",
]
