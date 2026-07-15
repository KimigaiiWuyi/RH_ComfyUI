"""语音情绪归一化 — 模态级共享工具(无外部依赖,纯函数)

不同 TTS 上游表达情绪的方式互斥:
- 内联标签(inline_bracket):情绪写在正文里 `[happy] ...`,支持句中定位与叠加;
- 自然语言(natural_language):情绪走一条独立的自由文本指令通道;
- 固定枚举(enum):情绪必须落在一个有限集合内,集合外一律丢弃;
- 无(none):不吃情绪。

基类 DigitalHumanSpeechBase 按各模型声明的 EmotionStyle 调用本模块,把
(正文, 情绪) 归一化成上游能直接消费的形态。子类只声明风格,无需重写逻辑。
"""

from __future__ import annotations

import re
from enum import Enum


class EmotionStyle(str, Enum):
    """模型消费情绪的方式(决定内联标签是嵌入还是剥离)"""

    INLINE_BRACKET = "inline_bracket"  # 正文内联 [tag];结构化情绪并入句首
    NATURAL_LANGUAGE = "natural_language"  # 情绪走独立自由文本;正文内联标记剥离
    ENUM = "enum"  # 情绪收敛到固定枚举;正文内联标记剥离
    NONE = "none"  # 不支持情绪;正文内联标记剥离


# 中文情绪 → 通用英文标签。既供内联翻译(zh→en),也供枚举收敛前的归一。
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

# 英文情绪/语气/拟声标签(内联剥离时判定 tag-like 用;非穷举,足以覆盖常见误写)
_EN_TAGS: frozenset[str] = frozenset(
    {
        "happy",
        "sad",
        "angry",
        "excited",
        "calm",
        "nervous",
        "confident",
        "surprised",
        "fearful",
        "disgusted",
        "fluent",
        "gentle",
        "playful",
        "serious",
        "whisper",
        "whispering",
        "shouting",
        "screaming",
        "sobbing",
        "crying",
        "laughing",
        "chuckling",
        "sighing",
        "groaning",
        "panting",
        "yawning",
        "emphasis",
        "break",
        "long-break",
        "breath",
        "cough",
        "sigh",
    }
)

# [tag] 或 (tag):括号内 1~24 个非括号字符。跨括号(如 "[a)")也收,少见但无害。
_BRACKET_RE = re.compile(r"[\[\(]([^\]\)]{1,24})[\]\)]")
# 纯英文单词/短语(允许空格与连字符),用于判定 tag-like
_ASCII_TAG_RE = re.compile(r"[A-Za-z][A-Za-z \-]*")
_MULTISPACE_RE = re.compile(r"\s{2,}")


def _normalize_word(word: str) -> str:
    """去括号/空白后把中文情绪词映射成英文标签;未知词原样返回"""
    key = word.strip().strip("[]()").strip()
    return _ZH_TO_TAG.get(key, key)


def _looks_like_tag(inner: str) -> bool:
    """括号内容是否像情绪/语气标记(而非用户想读出来的普通括注)"""
    text = inner.strip()
    if not text:
        return False
    if text in _ZH_TO_TAG:
        return True
    lowered = text.lower()
    if lowered in _EN_TAGS:
        return True
    # 纯英文短词按标记处理(如 "in a hurry tone");中文非情绪词(如 苹果/重要)保留
    return bool(_ASCII_TAG_RE.fullmatch(text)) and len(text) <= 24


def translate_inline_zh_tags(text: str) -> str:
    """把正文里 [中文情绪] 翻成 [english](供 inline_bracket 模型)

    仅翻译已知情绪词;非情绪括注(如 (苹果))与英文标签原样保留 ——
    inline_bracket 上游本就支持自由英文标签。
    """

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if inner in _ZH_TO_TAG:
            return f"[{_ZH_TO_TAG[inner]}]"
        return match.group(0)

    return _BRACKET_RE.sub(repl, text)


def strip_inline_tags(text: str) -> tuple[str, list[str]]:
    """剥离正文里 tag-like 的 [..]/(..) 标记,避免被上游当普通文字读出

    Returns:
        (清理后文本, 被剥离的标记原始内容列表)。普通括注不剥离。
    """
    stripped: list[str] = []

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if _looks_like_tag(inner):
            stripped.append(inner)
            return ""
        return match.group(0)

    cleaned = _BRACKET_RE.sub(repl, text)
    cleaned = _MULTISPACE_RE.sub(" ", cleaned).strip()
    return cleaned, stripped


def to_inline_tag(mood: str) -> str:
    """把结构化情绪包成内联标签:已带括号原样;中文→英文;其余作自由英文标签"""
    text = mood.strip()
    if text.startswith("[") or text.startswith("("):
        return text
    return f"[{_normalize_word(text)}]"


def to_enum_emotion(mood: str | None, extra: list[str], allowed: list[str]) -> str | None:
    """把结构化情绪或剥出的内联标记收敛到模型枚举;都不在枚举内则返回 None(丢弃)

    先取结构化 mood,再取正文里剥出的标记;逐个做 zh→en 归一后匹配枚举。
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
    "translate_inline_zh_tags",
    "strip_inline_tags",
    "to_inline_tag",
    "to_enum_emotion",
]
