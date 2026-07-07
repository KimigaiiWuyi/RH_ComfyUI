"""智能路由器 — 将 GenerationRequest 路由到最合适的节点

路由模型(本版核心):不再按 文生图/图生图/编辑、文生/图生/首尾帧/多模态 这种
细分任务强制分类,而是:

1. 粗粒度按"输出模态"(TaskType: IMAGE/VIDEO/MUSIC/SPEECH)圈定候选桶
2. 桶内按"输入档案"匹配:节点用 inputs 端口声明它接受的输入形状
   (图片数量 min/max、是否接受音/视频参考),只有与请求实际输入兼容的节点入选
3. 可用性过滤:对候选逐个 check_available()(纯配置读取,开销很低),剔除未配置后端
4. AI Agent 推荐(可选)
5. 优先级 + 随机:从可用候选里按 capabilities.priority 取最高优先级组,组内随机

策略顺序:
- 用户显式 model → 精确匹配 / 输入感知的部分名匹配,并校验可用性
- 否则走上面 2~5
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any, Optional

from gsuid_core.logger import logger

from .request import TASK_DISPLAY_NAME, TaskType, GenerationRequest
from .pipeline import NodeDef, PipelineRegistry, pipeline_registry

if TYPE_CHECKING:
    from ..backends import AdapterRegistry


class ModelUnavailableError(Exception):
    """该任务类型没有可用模型"""

    def __init__(self, task_type: TaskType, reason: str) -> None:
        self.task_type = task_type
        self.reason = reason
        super().__init__(f"任务类型 {task_type.value} 无可用模型: {reason}")


# ═══════════════════════════════════════════════════════════════════════
#  输入档案匹配
# ═══════════════════════════════════════════════════════════════════════

_IMAGE_PORT_KEYS = ("images", "image", "image_url")


def _image_port(node: NodeDef) -> Optional[Any]:
    for key in _IMAGE_PORT_KEYS:
        port = node.inputs.get(key)
        if port is not None:
            return port
    return None


def _node_supports_request(node: NodeDef, request: GenerationRequest) -> bool:
    """判断节点的输入档案是否兼容请求的实际输入形状

    节点没有显式声明 inputs 时视为完全兼容(向后兼容老 YAML)。
    """
    if not node.inputs:
        return True

    # ── prompt ──
    prompt_port = node.inputs.get("prompt")
    if prompt_port is not None and prompt_port.required and not request.prompt:
        return False

    # ── 图片数量(输入档案的核心:0=文生 / 1=图生 / 2+=首尾帧 …) ──
    n_img = len(request.images)
    img_port = _image_port(node)
    if n_img > 0:
        # 请求带图,但节点不接受图片 → 不兼容(例如纯文生图模型)
        if img_port is None:
            return False
        if img_port.max_items is not None and n_img > img_port.max_items:
            return False
        min_items = img_port.min_items if img_port.min_items is not None else (1 if img_port.required else 0)
        if n_img < min_items:
            return False
    else:
        # 请求不带图,但节点要求必须有图(编辑/图生视频等) → 不兼容
        if img_port is not None:
            needs_image = img_port.required or (img_port.min_items or 0) >= 1
            if needs_image:
                return False

    # ── 音/视频参考(多模态生视频) ──
    if request.audio_refs and "audio_refs" not in node.inputs:
        return False
    if request.video_refs and "video_refs" not in node.inputs:
        return False

    return True


# ═══════════════════════════════════════════════════════════════════════
#  可用性
# ═══════════════════════════════════════════════════════════════════════


async def _is_available(node: NodeDef, backend_registry: "AdapterRegistry") -> bool:
    adapter = backend_registry.get(node.backend)
    if adapter is None:
        return False
    try:
        return await adapter.check_node_available(node)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Router] {node.backend} check_node_available 异常: {e}")
        return False


async def _collect_unavailable_reasons(
    candidates: list[NodeDef],
    backend_registry: "AdapterRegistry",
) -> str:
    seen: dict[str, str] = {}
    for node in candidates:
        adapter = backend_registry.get(node.backend)
        if adapter is None:
            seen[node.backend] = f"后端 {node.backend} 未注册"
            continue
        try:
            reason = await adapter.get_node_unavailable_reason(node)
        except Exception:  # noqa: BLE001
            reason = f"后端 {node.backend} 不可用"
        # 同一后端的不同节点可能给出不同原因(如固定供应商未配置),按原因去重
        seen[reason] = reason
    return "\n".join(f"- {reason}" for reason in seen.values())


# ═══════════════════════════════════════════════════════════════════════
#  路由主流程
# ═══════════════════════════════════════════════════════════════════════


async def route(
    request: GenerationRequest,
    registry: Optional[PipelineRegistry] = None,
) -> NodeDef:
    """将 GenerationRequest 路由到最合适的节点"""
    if registry is None:
        registry = pipeline_registry

    # 懒加载兜底:热重载或启动钩子未执行导致注册表为空时,补一次加载
    if not registry.all_pipelines():
        _ensure_runtime_initialized(registry)

    from ..backends import backend_registry

    # ── 1. 用户显式指定 model ──
    if request.model:
        node = registry.get(request.model)
        if node is not None and node.task_type == request.task_type and _node_supports_request(node, request):
            if await _is_available(node, backend_registry):
                logger.info(f"[Router] 用户指定模型: {node.name}")
                return node
            logger.warning(f"[Router] 用户指定模型 {node.name} 不可用,回退自动选择")
        else:
            # 输入感知的部分名匹配:在当前模态、兼容输入的节点里挑名字含该 token、优先级最高的
            partial = request.model.lower()
            matches = [
                n
                for n in registry.get_by_task(request.task_type)
                if partial in n.name.lower() and _node_supports_request(n, request)
            ]
            matches.sort(key=lambda n: n.capabilities.priority, reverse=True)
            for n in matches:
                if await _is_available(n, backend_registry):
                    logger.info(f"[Router] 模糊匹配模型: {request.model} → {n.name}")
                    return n
            if matches:
                logger.warning(f"[Router] 模糊匹配到 {request.model},但均不可用,回退自动选择")

    # ── 2. 候选(同模态 + 输入档案兼容) ──
    candidates = [n for n in registry.get_by_task(request.task_type) if _node_supports_request(n, request)]
    if not candidates:
        raise ModelUnavailableError(
            request.task_type,
            f"没有与当前输入匹配的节点(图片数={len(request.images)})",
        )

    # ── 3. 可用性过滤 ──
    available: list[NodeDef] = []
    for n in candidates:
        if await _is_available(n, backend_registry):
            available.append(n)

    # ── 4. AI Agent 推荐(可选,仅在可用候选里推荐) ──
    if available:
        agent_choice = await _try_agent_recommendation(request, available)
        if agent_choice is not None:
            return agent_choice

    if not available:
        reasons = await _collect_unavailable_reasons(candidates, backend_registry)
        raise ModelUnavailableError(
            request.task_type,
            f"所有 {len(candidates)} 个候选后端均不可用:\n{reasons}",
        )

    # ── 5. 优先级 + 随机 ──
    available.sort(key=lambda n: n.capabilities.priority, reverse=True)
    top_priority = available[0].capabilities.priority
    top_group = [n for n in available if n.capabilities.priority == top_priority]
    selected = random.choice(top_group)
    logger.info(f"[Router] 优先级选择: {selected.name} (priority={top_priority})")
    return selected


async def _try_agent_recommendation(
    request: GenerationRequest,
    candidates: list[NodeDef],
) -> NodeDef | None:
    """尝试通过 AI Agent 推荐节点

    延迟导入 gsuid_core.ai_core.gs_agent.recommend_model。
    如果该符号不存在或其依赖未安装(典型:pydantic_ai_skills 缺失),
    则安全降级,返回 None 让路由器继续走确定性路由逻辑。
    """
    try:
        from gsuid_core.ai_core import gs_agent  # type: ignore[attr-defined]

        recommend_model: Any = getattr(gs_agent, "recommend_model", None)
        if recommend_model is None:
            return None

        result = await recommend_model(request, candidates)
        if result is not None:
            return result
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Router] AI Agent 推荐失败: {e}")
    return None


def get_display_name(task_type: TaskType) -> str:
    """获取任务类型的中文显示名"""
    return TASK_DISPLAY_NAME.get(task_type, task_type.value)


def _ensure_runtime_initialized(registry: PipelineRegistry) -> None:
    """注册表为空时的懒加载兜底(热重载 / 启动钩子未执行)"""
    from ..backends import init_backends, backend_registry

    if not backend_registry.all():
        init_backends()

    if registry.all_pipelines():
        return

    # 2026-07 起模型定义全编程式:懒加载兜底改走 discover_builtin_models
    from ...models import discover_builtin_models

    discover_builtin_models()
    logger.info(f"[Router] 懒加载节点完成: {len(registry.all_pipelines())} 个")
