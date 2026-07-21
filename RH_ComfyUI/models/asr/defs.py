"""models/asr/defs.py — 编程式 ASR 模型定义

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件。
"""

from __future__ import annotations

from .overrides import FishAsrModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.fishaudio_asr import fishaudio_asr_mapper as _fishaudio_asr_mapper
from ...utils.mappers.fishaudio_asr_billing import estimate_fish_asr_points


class FishAsrDef(FishAsrModel):
    """Fish Audio ASR — 语音识别(音频 → 文本 + 时间戳分段)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="fish_asr",
            display_name="Fish Audio 语音识别",
            task_type=TaskType("asr"),
            backend="fishaudio",
            point_cost=2,
            description="Fish Audio ASR 语音识别:多语言自动识别,带时间戳分段,适合口播/字幕/转写场景",
            knowledge_content=(
                "Fish Audio ASR 语音识别模型。"
                "\n"
                "优势:多语言自动识别,返回带时间戳的分段,适合做字幕与口播转写。"
                "\n"
                "约束:单文件 ≤ 20MB / 60min,支持 wav/mp3/opus 等格式。"
                "\n"
                "适用场景:音频转写为文本、口播字幕生成、有声内容归档。"
                "\n"
                "不适用场景:实时流式识别(走异步轮询也不在此列)。"
                "\n"
            ),
            requirements=["fishaudio_apikey"],
            mode="programmatic",
            mapper_func=_fishaudio_asr_mapper,
            inputs={
                "audio_payload": PortSpec(
                    type=PortType.AUDIO,
                    required=True,
                    title="待转写音频",
                    description="待转写的音频文件(wav/mp3/opus 等),≤20MB / 60min",
                ),
                "language": PortSpec(
                    type=PortType.STRING,
                    title="语言",
                    description="ISO 639-1 语言码(如 en/zh/ja),留空由上游自动识别",
                ),
                "include_timestamps": PortSpec(
                    type=PortType.BOOLEAN,
                    default=True,
                    title="包含时间戳",
                    description="是否返回 segments(带 start/end 时间戳的分段);关闭只返回全文文本",
                ),
                "params": PortSpec(
                    type=PortType.STRING,
                    title="扩展参数",
                    description="预留扩展(后端私有参数透传)",
                ),
            },
            outputs={
                "text": PortSpec(type=PortType.OUTPUT_TEXT, description="转写出的全文文本(UTF-8)"),
                "segments": PortSpec(
                    type=PortType.OUTPUT_TEXT,
                    required=False,
                    description="带时间戳的分段(JSON 序列化后的字符串)",
                ),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["asr"],
                mode="sync",
                priority=80,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输入音频时长计费(0.36 美元 / 音频小时)。"""
        audio = request.audio_payload
        if not audio and request.audio_refs:
            audio = request.audio_refs[0].data if request.audio_refs[0].data else None
        return estimate_fish_asr_points(audio)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(1 秒音频) ~ 最大(60 分钟音频上限)。"""
        from RH_ComfyUI.utils.mappers.fishaudio_asr_billing import calculate_asr_points

        # 1 秒 @ 128kbps = 16_000 bytes
        min_audio = b"\x00" * 16_000
        # 60 分钟 @ 128kbps = 57_600_000 bytes
        max_audio = b"\x00" * 57_600_000
        return (
            calculate_asr_points(min_audio),
            calculate_asr_points(max_audio),
        )


ALL_MODELS = [FishAsrDef]
