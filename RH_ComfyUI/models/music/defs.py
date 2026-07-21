"""models/music/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from ..bridge import MusicPipelineModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.music import ace_step_mapper as _ace_step_mapper


class AceStep15Def(MusicPipelineModel):
    """ACE-Step 1.5 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="ace_step1.5",
            display_name="ACE-Step 1.5",
            task_type=TaskType("music"),
            backend="comfyui",
            point_cost=2,
            description="音乐生成模型，支持多种风格和歌词输入",
            knowledge_content=(
                "音乐生成模型。"
                "\n"
                "优势：可以生成多种风格音乐，支持歌词输入，音乐质量较高。"
                "\n"
                "适用场景：背景音乐生成，创意音乐制作，配乐需求。"
                "\n"
                "不适用场景：语音合成（建议用 IndexTTS2）。"
                "\n"
            ),
            requirements=["comfyui_url"],
            workflow_file="ace_step1.5.json",
            mode="programmatic",
            mapper_func=_ace_step_mapper,
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT, required=True, title="风格描述", description="音乐风格/主题描述"
                ),
                "negative_prompt": PortSpec(type=PortType.TEXT, title="歌词", description="歌词内容,可选"),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的音频"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["music"],
                mode="sync",
                max_concurrency=1,  # 本地 ComfyUI 共用一块 GPU,工作流必须串行
                priority=70,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """固定价格:10 积分/次。"""
        return 10

    def point_range(self) -> tuple[int, int]:
        """积分范围:固定值(min=max=10)。"""
        return (10, 10)


ALL_MODELS = [AceStep15Def]
