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
    2. 精确匹配 > 前缀匹配 > 包含匹配 > 归一化匹配
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


# ── 尺寸与比例自适应解析 ──

_EXPLICIT_AR_FLAG_RE = re.compile(
    r"(?:--ar|-ar|--aspect|aspect|--ratio|ratio|比例|宽高比)\s*[:=]?\s*(\d{1,2}\s*[:/]\s*\d{1,2})",
    re.IGNORECASE,
)
_EXPLICIT_SIZE_FLAG_RE = re.compile(
    r"(?:--size|-size|size|尺寸|分辨率)\s*[:=]?\s*(\d{3,5})\s*[*xX×]\s*(\d{3,5})",
    re.IGNORECASE,
)
_STANDALONE_RATIO_RE = re.compile(
    r"(?<![\d\w])(16:9|9:16|4:3|3:4|1:1|21:9|9:21|3:2|2:3|4:5|5:4|1:2|2:1)(?![\d\w])",
    re.IGNORECASE,
)
_STANDALONE_SIZE_RE = re.compile(
    r"(?<![\d\w])(\d{3,5})\s*[*xX×]\s*(\d{3,5})(?![\d\w])",
    re.IGNORECASE,
)

_SEMANTIC_RATIOS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:横版|横屏|宽幅|宽屏|横向|横构图|电脑壁纸|桌面壁纸)"), "16:9"),
    (re.compile(r"(?:竖版|竖屏|手机壁纸|手机立绘|竖向|竖构图|立绘)"), "9:16"),
    (re.compile(r"(?:正方形|方形|方图|头像|方形构图|方构图)"), "1:1"),
]


def _ratio_from_size(w: int, h: int) -> Optional[str]:
    """根据像素宽高就近推算标准比例"""
    if h <= 0 or w <= 0:
        return None
    r = w / h
    candidates = [
        ("1:1", 1.0),
        ("16:9", 16 / 9),
        ("9:16", 9 / 16),
        ("4:3", 4 / 3),
        ("3:4", 3 / 4),
        ("21:9", 21 / 9),
        ("9:21", 9 / 21),
        ("3:2", 3 / 2),
        ("2:3", 2 / 3),
        ("4:5", 4 / 5),
        ("5:4", 5 / 4),
    ]
    for name, val in candidates:
        if abs(r - val) < 0.05:
            return name
    return None


def extract_aspect_ratio_and_size(
    text: str,
) -> tuple[str, Optional[str], Optional[tuple[int, int]]]:
    """从用户 prompt 中自适应提取比例与尺寸，并清洗参数标记。

    Args:
        text: 提示词文本

    Returns:
        (cleaned_prompt, ratio, size)
    """
    if not text:
        return text, None, None

    cleaned = text
    ratio: Optional[str] = None
    size: Optional[tuple[int, int]] = None

    # 1. 显式 size 命令行参数
    size_flag_match = _EXPLICIT_SIZE_FLAG_RE.search(cleaned)
    if size_flag_match:
        w_str, h_str = size_flag_match.group(1), size_flag_match.group(2)
        try:
            w, h = int(w_str), int(h_str)
            if 64 <= w <= 8192 and 64 <= h <= 8192:
                size = (w, h)
                ratio = _ratio_from_size(w, h)
                cleaned = _EXPLICIT_SIZE_FLAG_RE.sub("", cleaned).strip()
        except ValueError:
            pass

    # 2. 显式 ratio 命令行参数
    ar_flag_match = _EXPLICIT_AR_FLAG_RE.search(cleaned)
    if ar_flag_match:
        raw_ar = ar_flag_match.group(1).replace("/", ":").replace(" ", "")
        ratio = raw_ar
        cleaned = _EXPLICIT_AR_FLAG_RE.sub("", cleaned).strip()

    # 3. 独立尺寸（如果未提取到 size）
    if size is None:
        standalone_size_match = _STANDALONE_SIZE_RE.search(cleaned)
        if standalone_size_match:
            try:
                w = int(standalone_size_match.group(1))
                h = int(standalone_size_match.group(2))
                if 256 <= w <= 8192 and 256 <= h <= 8192:
                    size = (w, h)
                    if ratio is None:
                        ratio = _ratio_from_size(w, h)
            except ValueError:
                pass

    # 4. 独立比例字串（如 "16:9", "9:16"）
    if ratio is None:
        standalone_ar_match = _STANDALONE_RATIO_RE.search(cleaned)
        if standalone_ar_match:
            ratio = standalone_ar_match.group(1).replace("/", ":")

    # 5. 自然语言构图语义词（如 "横版", "立绘", "头像"）
    if ratio is None:
        for pat, mapped_ratio in _SEMANTIC_RATIOS:
            if pat.search(cleaned):
                ratio = mapped_ratio
                break

    # 归一化空格
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, ratio, size


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
