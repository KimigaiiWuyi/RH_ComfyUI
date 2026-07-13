"""ModelCard — 模型自描述元数据,服务 Agent 智能选型 / HTTP 清单 / 知识库注册"""

from __future__ import annotations

from typing import Any
from dataclasses import field, dataclass


@dataclass(frozen=True)
class ModelCard:
    """模型的"名片",全部字段面向人类与 LLM 可读

    Attributes:
        description:  一句话简介(HTTP 清单 description 字段沿用)
        strengths:    优势列表,如 ["中文语义理解强", "动作流畅"]
        categories:   适用品类标签,如 ["写实", "二次元", "短视频"]
        weaknesses:   不擅长的场景(帮 Agent 排除)
        sample_prompts: 1~3 条示例提示词(注入知识库)
        languages:    优势语言,如 ["zh", "en"]
        speed_hint:   速度提示: "fast" / "normal" / "slow"
    """

    description: str
    strengths: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    sample_prompts: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    speed_hint: str = "normal"

    def to_knowledge_text(self, *, name: str, display_name: str, point_cost: int) -> str:
        """渲染为 LLM 友好的知识条目(供 ai_entity 注册与路由推荐)"""
        lines = [
            f"# {display_name} (id={name})",
            f"简介: {self.description}",
            f"积分消耗: {point_cost}",
        ]
        if self.strengths:
            lines.append("优势: " + "、".join(self.strengths))
        if self.categories:
            lines.append("适用品类: " + "、".join(self.categories))
        if self.weaknesses:
            lines.append("不适用: " + "、".join(self.weaknesses))
        if self.sample_prompts:
            lines.append("示例提示词: " + " | ".join(self.sample_prompts))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """HTTP 清单序列化(/api/RH_ComfyUI/models 的新增 card 字段)"""
        return {
            "description": self.description,
            "strengths": list(self.strengths),
            "categories": list(self.categories),
            "weaknesses": list(self.weaknesses),
            "sample_prompts": list(self.sample_prompts),
            "languages": list(self.languages),
            "speed_hint": self.speed_hint,
        }


__all__ = ["ModelCard"]
