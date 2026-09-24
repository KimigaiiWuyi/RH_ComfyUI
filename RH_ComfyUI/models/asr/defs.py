"""models/asr/defs.py — 编程式 ASR 模型定义

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件。
"""

from __future__ import annotations

from .overrides import FishAsrModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...rh_config.fish_models import FISH_ASR_MODELS, FishModelSpec
from ...utils.mappers.fishaudio_asr import fishaudio_asr_mapper_for as _fishaudio_asr_mapper_for
from ...utils.mappers.fishaudio_asr_billing import calculate_asr_points, estimate_fish_asr_points


def _fish_asr_knowledge(spec: FishModelSpec) -> str:
    extra = ""
    if spec.name == "transcribe-1-pro":
        extra = "Pro 档在文本中保留说话人标记与情绪提示。\n"
    return (
        f"{spec.display_name}({spec.name})。"
        "\n"
        "优势:多语言自动识别,可返回带时间戳的分段。"
        "\n"
        f"{extra}"
        "约束:单文件 ≤ 20MB / 60min,支持 wav/mp3/opus。"
        "\n"
        "适用场景:音频转写为文本、口播字幕、有声内容归档。"
        "\n"
        "不适用场景:实时流式识别。"
        "\n"
    )


def _asr_point_range() -> tuple[int, int]:
    # 1 秒 / 60 分钟 @ 128kbps,与时长计费的上下界对齐
    return (calculate_asr_points(b"\x00" * 16_000), calculate_asr_points(b"\x00" * 57_600_000))


def _build_fish_asr(spec: FishModelSpec) -> type[FishAsrModel]:
    """同一 ASR 执行链按上游 model header 拆成可独立启停的模型。"""
    engine = spec.name

    class Def(FishAsrModel):
        def __init__(self) -> None:
            super().__init__(self.node_def())

        @staticmethod
        def node_def() -> NodeDef:
            return NodeDef(
                name=engine,
                display_name=spec.display_name,
                task_type=TaskType("asr"),
                backend="fishaudio",
                backend_model=engine,
                point_cost=2,
                description=spec.description,
                knowledge_content=_fish_asr_knowledge(spec),
                requirements=["fishaudio_apikey"],
                mode="programmatic",
                mapper_func=_fishaudio_asr_mapper_for(engine),
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
                    priority=spec.priority,
                ),
            )

        def estimate_cost(self, request: GenerationRequest) -> int:
            audio = request.audio_payload
            if not audio and request.audio_refs:
                audio = request.audio_refs[0].data if request.audio_refs[0].data else None
            return estimate_fish_asr_points(audio)

        def point_range(self) -> tuple[int, int]:
            return _asr_point_range()

    Def.__name__ = "FishAsr_" + engine.replace(".", "_").replace("-", "_")
    Def.__qualname__ = Def.__name__
    return Def


_FISH_ASR_BY_NAME: dict[str, type[FishAsrModel]] = {spec.name: _build_fish_asr(spec) for spec in FISH_ASR_MODELS}
FishAsrDef = _FISH_ASR_BY_NAME["transcribe-1"]

ALL_MODELS = list(_FISH_ASR_BY_NAME.values())
