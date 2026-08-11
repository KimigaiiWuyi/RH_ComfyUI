"""命令解析器 — 从用户输入中提取可选模型名和实际 prompt"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

from gsuid_core.logger import logger

from .request import TaskType

if TYPE_CHECKING:
    from .pipeline import PipelineRegistry, pipeline_registry
else:
    pipeline_registry = None  # type: ignore[assignment]


def _ensure_registry_loaded(registry: PipelineRegistry) -> None:
    """确保 Pipeline 注册表已初始化（懒加载兜底）

    所有重依赖都使用函数内延迟导入，以避免和 backends/ 子包之间的循环。
    """
    if registry.all_pipelines():
        return

    # 延迟导入以打破循环:core/parser → backends → base → core/types
    from .pipeline import pipeline_registry as _registry  # noqa: PLC0415
    from ..backends import init_backends, backend_registry  # noqa: PLC0415

    if not backend_registry.all():
        init_backends()

    # 2026-07 起模型定义全编程式:懒加载兜底改走 discover_builtin_models
    from ...models import discover_builtin_models  # noqa: PLC0415

    discover_builtin_models()

    logger.info(f"[Parser] 懒加载 Pipeline 完成: {len(_registry.all_pipelines())} 个")


def parse_model_from_prompt(
    text: str,
    task_type: TaskType,
    registry: Optional[PipelineRegistry] = None,
) -> tuple[Optional[str], str]:
    """从用户输入中解析可选的模型名和实际 prompt

    解析规则：
    1. 提取第一个词，检查是否匹配已知 Pipeline 名
    2. 精确匹配 > 前缀匹配 > 包含匹配
    3. 不匹配则整个文本作为 prompt

    Args:
        text: 用户输入的原始文本（去掉命令关键词后）
        task_type: 当前任务类型
        registry: Pipeline 注册表（默认使用全局单例）

    Returns:
        (model_name_or_None, actual_prompt)
    """
    # 模块级懒加载：避免循环导入
    global pipeline_registry
    if pipeline_registry is None:
        from .pipeline import pipeline_registry as _registry

        pipeline_registry = _registry

    if registry is None:
        registry = pipeline_registry

    # 懒加载兜底：确保注册表已初始化
    _ensure_registry_loaded(registry)

    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None, ""

    first_word = parts[0].lower()

    # 仅判断 first_word 是否是有效模型 token(是则从 prompt 中剥离),
    # 但返回原始 token 而非解析后的具体节点名 —— 真正的节点选择交给 router,
    # 这样 router 能结合实际输入(图片数量等)做输入感知的解析,
    # 避免 "qwen" 在带图编辑场景被错配到纯文生图的 qwen_2512。
    pipeline = registry.find_by_partial_name(first_word, task_type)
    if pipeline:
        actual_prompt = parts[1] if len(parts) > 1 else ""
        logger.info(
            f"[Parser] 识别到模型 token: '{first_word}'(候选示例 {pipeline.name}), prompt={actual_prompt[:30]}..."
        )
        return first_word, actual_prompt

    # 不匹配任何模型名，整个文本作为 prompt
    # 调试：列出该任务类型的所有 Pipeline 名
    all_names = [p.name for p in registry.get_by_task(task_type)]
    logger.warning(f"[Parser] 模型名 '{first_word}' 未匹配，该任务类型可用模型: {all_names}")
    return None, text.strip()


# MiniMax T2A 支持的情绪标签
MINIMAX_EMOTIONS = {
    "happy",
    "sad",
    "angry",
    "fearful",
    "disgusted",
    "surprised",
    "calm",
    "fluent",
    "whisper",
}

# 情绪标签解析正则（支持 [情绪] / [情绪:xxx]，含全角 【情绪】/［情绪］ 格式）

_MOOD_BRACKET_RE = re.compile(r"^[\[【［]([^\]】］]+)[\]】］]\s*")


def parse_mood_from_prompt(text: str) -> tuple[Optional[str], str]:
    """从文本开头解析可选的情绪标签

    支持的格式：
    1. [高兴] 实际文本 → mood="高兴"
    2. [happy] 实际文本 → mood="happy"
    3. [情绪:开心的] 实际文本 → mood="开心的"
    4. 实际文本（无情绪标签） → mood=None

    Args:
        text: 经过模型名解析后的剩余文本

    Returns:
        (mood_or_None, actual_text)
    """
    if not text:
        return None, text

    match = _MOOD_BRACKET_RE.match(text)
    if not match:
        return None, text

    mood_raw = match.group(1).strip()

    # 支持 [情绪:xxx] 格式，提取冒号后面的部分
    if mood_raw.startswith("情绪:"):
        mood = mood_raw[3:].strip()
    elif mood_raw.startswith("mood:"):
        mood = mood_raw[5:].strip()
    else:
        mood = mood_raw

    remaining = text[match.end() :]
    logger.info(f"[Parser] 情绪标签解析成功: '{mood}', text={remaining[:30]}...")
    return mood, remaining
