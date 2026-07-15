"""语音情绪归一化 — 模态级共享工具(无外部依赖,纯函数)

情绪只来自**显式情绪块** `<<EMO: label>>`(由调用方的情绪选单产生),而**不是**
正文里字面的 `[..]` / `【..】` —— 后者一律当普通文本(用户复制/手打的括号不受影响)。

不同 TTS 上游消费情绪的方式互斥,由各模型声明 EmotionStyle,基类据此把
(正文, 情绪块, 结构化情绪 mood) 归一化成上游能直接消费的形态:
- 内联(inline_bracket):情绪块就地展开为 `[english]`,支持句中定位与叠加;
- 自然语言(natural_language):情绪走独立自由文本字段;情绪块剥离;
- 固定枚举(enum):情绪收敛到有限集合;情绪块剥离;
- 无(none):不吃情绪;情绪块剥离。
"""

from __future__ import annotations

import re
from enum import Enum


class EmotionStyle(str, Enum):
    """模型消费情绪的方式(决定显式情绪块是内联展开还是剥离)"""

    INLINE_BRACKET = "inline_bracket"  # 情绪块 → 内联 [tag];结构化情绪并入句首
    NATURAL_LANGUAGE = "natural_language"  # 情绪走独立自由文本;情绪块剥离
    ENUM = "enum"  # 情绪收敛到固定枚举;情绪块剥离
    NONE = "none"  # 不支持情绪;情绪块剥离


# 中文情绪 → 通用英文标签。供内联展开(zh→en)与枚举收敛前的归一。
_ZH_TO_TAG: dict[str, str] = {
    "高兴": "happy",
    "开心": "happy",
    "快乐": "happy",
    "喜悦": "happy",
    "悲伤": "sad",
    "伤心": "sad",
    "难过": "sad",
    "愤怒": "angry",
    "生气": "angry",
    "恼怒": "angry",
    "恐惧": "fearful",
    "害怕": "fearful",
    "惊恐": "fearful",
    "厌恶": "disgusted",
    "嫌弃": "disgusted",
    "惊讶": "surprised",
    "吃惊": "surprised",
    "平静": "calm",
    "冷静": "calm",
    "中性": "calm",
    "兴奋": "excited",
    "激动": "excited",
    "紧张": "nervous",
    "焦虑": "nervous",
    "低语": "whisper",
    "耳语": "whisper",
    "轻声": "whisper",
    "喊叫": "shouting",
    "大喊": "shouting",
    "呐喊": "shouting",
    "尖叫": "screaming",
    "哭泣": "sobbing",
    "哭腔": "sobbing",
    "抽泣": "sobbing",
    "大笑": "laughing",
    "笑": "laughing",
    "轻笑": "chuckling",
    "叹气": "sighing",
    "叹息": "sighing",
    "呻吟": "groaning",
    "喘息": "panting",
    "打哈欠": "yawning",
    "自信": "confident",
    "温柔": "gentle",
    "俏皮": "playful",
    "严肃": "serious",
    "深沉": "deep",
    "委屈": "aggrieved",
    "着急": "in a hurry tone",
    "急促": "in a hurry tone",
    "生动": "fluent",
    "流畅": "fluent",
}

# 显式情绪块:`<<EMO: label>>`(与调用方情绪选单/序列化协议一致)。
_EMO_MARKER_RE = re.compile(r"<<EMO:\s*([^>]*?)\s*>>")
_MULTISPACE_RE = re.compile(r"\s{2,}")


def _normalize_word(word: str) -> str:
    """去空白后把中文情绪词映射成英文标签;未知词原样返回"""
    key = word.strip()
    return _ZH_TO_TAG.get(key, key)


def render_inline_markers(text: str) -> str:
    """把显式情绪块 `<<EMO: label>>` 就地展开为内联 `[english]`(中→英)

    正文里字面的 `[..]` / `【..】` 一律不动 —— 只有显式情绪块才是情绪。
    """

    def repl(match: re.Match[str]) -> str:
        return f"[{_normalize_word(match.group(1))}]"

    return _EMO_MARKER_RE.sub(repl, text)


def extract_emotion_markers(text: str) -> tuple[str, list[str]]:
    """剥离显式情绪块,返回(清理后文本, 块内标签原文列表)

    普通括号文本不受影响。用于非内联模型:块内情绪交由 mood / 枚举通道消费。
    """
    labels: list[str] = []

    def repl(match: re.Match[str]) -> str:
        labels.append(match.group(1).strip())
        return ""

    cleaned = _EMO_MARKER_RE.sub(repl, text)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned).strip()
    return cleaned, labels


def to_inline_tag(mood: str) -> str:
    """把结构化情绪包成内联标签:已带括号原样;中文→英文;其余作自由英文标签"""
    text = mood.strip()
    if text.startswith("[") or text.startswith("("):
        return text
    return f"[{_normalize_word(text)}]"


def to_enum_emotion(mood: str | None, extra: list[str], allowed: list[str]) -> str | None:
    """把结构化情绪或情绪块标签收敛到模型枚举;都不在枚举内则返回 None(丢弃)

    先取结构化 mood,再取正文里剥出的情绪块标签;逐个做 zh→en 归一后匹配枚举。
    """
    allowed_set = set(allowed)
    candidates: list[str] = []
    if mood:
        candidates.append(mood)
    candidates.extend(extra)
    for candidate in candidates:
        tag = _normalize_word(candidate)
        if tag in allowed_set:
            return tag
    return None


__all__ = [
    "EmotionStyle",
    "render_inline_markers",
    "extract_emotion_markers",
    "to_inline_tag",
    "to_enum_emotion",
]
