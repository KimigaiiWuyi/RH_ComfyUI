"""models/speech/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass

from ..bridge import SpeechPipelineModel
from .overrides import (
    FishTtsModel,
    QwenTtsModel,
    GeminiTtsModel,
    IndexTTS2Model,
    IndexTTS25Model,
    MinimaxSpeechModel,
)
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.speech import index_tts2_mapper as _index_tts2_mapper
from ...rh_config.fish_models import FISH_TTS_MODELS, FishModelSpec
from ...utils.mappers.qwen_tts import qwen_tts_mapper_for as _qwen_tts_mapper_for
from ...utils.mappers.gemini_tts import gemini_tts_mapper_for as _gemini_tts_mapper_for
from ...utils.mappers.mimo_speech import mimo_tts_mapper as _mimo_tts_mapper
from ...utils.mappers.extra_billing import estimate_mimo_tts_points, estimate_minimax_t2a_points
from ...utils.mappers.minimax_speech import minimax_t2a_speech_mapper as _minimax_t2a_speech_mapper
from ...utils.mappers.speech_billing import estimate_fish_tts_points, estimate_index_tts2_points
from ...utils.mappers.qwen_tts_voices import (
    QWEN_TTS_31,
    QWEN_TTS_PLUS,
    QWEN_TTS_FLASH,
    QWEN_VOICES_BY_MODEL,
    QWEN_DEFAULT_VOICE_BY_MODEL,
)
from ...utils.mappers.fishaudio_speech import fishaudio_tts_mapper_for as _fishaudio_tts_mapper_for
from ...utils.mappers.qwen_tts_billing import settle_qwen_tts_points, estimate_qwen_tts_points
from ...utils.mappers.gemini_tts_billing import settle_gemini_tts_points, estimate_gemini_tts_points
from ...utils.backends.gemini_speech.voices import (
    GEMINI_TTS_VOICES,
    GEMINI_TTS_DEFAULT_VOICE,
    GEMINI_TTS_UPSTREAM_MODEL,
)


class IndexTTS2Def(IndexTTS2Model):
    """IndexTTS2 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="IndexTTS2",
            display_name="IndexTTS2",
            task_type=TaskType("speech"),
            backend="comfyui",
            point_cost=2,
            description="中文发音自然",
            knowledge_content=(
                "语音合成模型。"
                "\n"
                "优势：语音自然，中文发音准确，支持多种语气。"
                "\n"
                "适用场景：文本转语音，有声内容制作，辅助阅读。"
                "\n"
                "不适用场景：音乐生成（建议用 ace_step1.5）。"
                "\n"
            ),
            requirements=["comfyui_url"],
            workflow_file="IndexTTS2.json",
            mode="programmatic",
            mapper_func=_index_tts2_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="合成文本", description="待合成文本"),
                "reference_audio": PortSpec(type=PortType.AUDIO, title="参考音频", description="参考音频,用于音色复刻"),
                "mood": PortSpec(type=PortType.STRING, title="情绪", description="情绪描述"),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["speech"],
                mode="sync",
                max_concurrency=1,  # 本地 ComfyUI 共用一块 GPU,工作流必须串行
                priority=80,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输入文本 UTF-8 字节长度计费(5 美元 / M bytes)。"""
        return estimate_index_tts2_points(request.prompt)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(空文本) ~ 最大(5000 字符常见上限)。

        历史 bug:max 用 300 字符(900 bytes)算出的积分还是 1,与 min=1 相同,
        前端用 min<max 判断是否调 estimate API,导致 IndexTTS2 被当成"固定积分"
        不调 estimate,实际生成长文本时积分远超显示。改成 5000 字符(15000 bytes)
        算出 8 积分,触发前端动态调用 estimate。

        上限取 5000 而非真实模型上限,是为了 UI 上展示"常见长度的最高积分"
        而非理论上限(避免误导用户以为输入多少字都是 8 积分)。
        """
        return (
            estimate_index_tts2_points(""),
            estimate_index_tts2_points("你" * 5000),
        )


class IndexTTS25Def(IndexTTS25Model):
    """IndexTTS2.5 — RunningHub AI App 2089692922081009666

    走 rh_app OpenAPI v2(nodeInfoList),凭证复用 RH_apikey。
    audio=参考音频, prompt=待合成文本, text=情绪(开心/高兴等,可空)。
    计费与 IndexTTS2 相同(5 美元 / M UTF-8 bytes)。
    """

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="IndexTTS2.5",
            display_name="IndexTTS2.5",
            task_type=TaskType("speech"),
            backend="rh_app",
            point_cost=2,
            description="克隆音色，带情绪",
            knowledge_content=(
                "IndexTTS2.5 是 RunningHub 上的 IndexTTS 2.5 语音合成应用。"
                "\n"
                "优势：语音自然，中文发音准确，支持参考音频音色克隆与自然语言情绪。"
                "\n"
                "输入：prompt 为要说的话；reference_audio 为参考音色(推荐提供)；"
                "mood 为情绪，如开心、高兴、悲伤，可留空。"
                "\n"
                "适用场景：文本转语音，有声内容制作，配音，辅助阅读。"
                "\n"
                "不适用场景：音乐生成（建议用 ace_step1.5）。"
                "\n"
                "凭证：复用 RH_apikey（RunningHub 通用 key）。"
                "\n"
            ),
            requirements=["rh_apikey"],
            workflow_file="2089692922081009666",
            mode="declarative",
            mappings=[
                {
                    "source": "reference_audio",
                    "target": "2.audio",
                    "type": "upload_audio",
                    "optional": True,
                    "description": "audio",
                },
                {
                    "source": "prompt",
                    "target": "6.prompt",
                    "description": "prompt",
                },
                {
                    "source": "mood",
                    "target": "8.text",
                    "default": "",
                    "description": "text",
                },
            ],
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="合成文本", description="待合成文本"),
                "reference_audio": PortSpec(type=PortType.AUDIO, title="参考音频", description="参考音频,用于音色复刻"),
                "mood": PortSpec(
                    type=PortType.STRING,
                    title="情绪",
                    description="情绪描述,如开心、高兴、悲伤,可留空",
                    values=["开心", "高兴", "悲伤", "愤怒", "惊讶", "平静", "兴奋", "温柔"],
                ),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["speech"],
                mode="async_poll",
                priority=75,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:与 IndexTTS2 相同,按输入文本 UTF-8 字节长度计费。"""
        return estimate_index_tts2_points(request.prompt)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(空文本) ~ 最大(5000 字符常见上限)。"""
        return (
            estimate_index_tts2_points(""),
            estimate_index_tts2_points("你" * 5000),
        )


class MimoTtsDef(SpeechPipelineModel):
    """MiMoTTS 语音合成 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="mimo_tts",
            display_name="MiMoTTS 语音合成",
            task_type=TaskType("speech"),
            backend="mimo",
            point_cost=3,
            description="预置音色与复刻",
            knowledge_content=(
                "XiaoMi MiMo-V2.5-TTS 是小米推出的高质量语音合成系列模型。"
                "\n"
                "支持三种模式："
                "\n"
                "1. mimo-v2.5-tts：预置精品音色，开箱即用，支持唱歌模式"
                "\n"
                "2. mimo-v2.5-tts-voicedesign：通过文本描述定制音色，无需预置或音频样本"
                "\n"
                "3. mimo-v2.5-tts-voiceclone：基于音频样本复刻任意音色"
                "\n"
                "风格控制能力："
                "\n"
                "- 多风格切换：同一段语音内完成播报→低语→嘶吼的风格转场"
                "\n"
                '- 多情绪混合：支持复合情绪如"压抑的愤怒"、"带着哽咽的笑意"'
                "\n"
                "- 自然语言控制：通过描述性语句控制语气、语速、情绪"
                "\n"
                "- 音频标签控制：支持 [吸气] [叹气] [笑] [哭泣] 等细粒度标签"
                "\n"
                "- 导演模式：从角色、场景、指导三个维度全方位刻画人物与声线"
                "\n"
                "支持的情绪/风格：开心/悲伤/愤怒/恐惧/惊讶/兴奋/委屈/平静/冷漠/温柔/高冷/"
                "\n"
                "活泼/严肃/慵懒/俏皮/深沉/干练/凌厉/磁性/醇厚/清亮/空灵/稚嫩/苍老/甜美/沙哑 等"
                "\n"
                "支持方言：东北话/四川话/河南话/粤语"
                "\n"
                "适用场景：配音、有声内容制作、角色扮演、唱歌、语音播报。"
                "\n"
            ),
            requirements=["mimo_apikey"],
            mode="programmatic",
            mapper_func=_mimo_tts_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="合成文本", description="待合成文本"),
                "mood": PortSpec(
                    type=PortType.STRING, title="情绪风格", description='情绪/风格控制指令,如 "用慵懒的语调"'
                ),
                "voice_id": PortSpec(
                    type=PortType.STRING, title="预置音色", description="预置音色 ID,可选,留空使用默认"
                ),
                "reference_audio": PortSpec(type=PortType.AUDIO, title="参考音频", description="参考音频,用于音色复刻"),
                "params": PortSpec(type=PortType.STRING, title="扩展参数", description="预留扩展,可指定 model 等"),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["speech"],
                mode="sync",
                priority=65,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输入文本 UTF-8 字节长度计费(6 美元/M bytes → 600 积分/M bytes)。"""
        return estimate_mimo_tts_points(request.prompt)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(空文本) ~ 最大(5000 字符常见上限)。

        历史 bug:同 IndexTTS2 —— max 用 300 字符算出仍是 1 积分,前端误判为固定积分。
        改为 5000 字符(15000 bytes × 600 积分/M bytes = 9 积分)触发动态 estimate。
        """
        return (
            estimate_mimo_tts_points(""),
            estimate_mimo_tts_points("你" * 5000),
        )


class MinimaxT2aSpeechDef(MinimaxSpeechModel):
    """MiniMax 语音合成 — 定义迁移自 pipelines YAML(2026-07 起以代码为准)"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="minimax_t2a_speech",
            display_name="MiniMax 语音合成",
            task_type=TaskType("speech"),
            backend="minimax",
            point_cost=3,
            description="情绪语速可调",
            knowledge_content=(
                "MiniMax T2A Async V2 是 MiniMax 推出的高质量异步语音合成模型。"
                "\n"
                "优势：语音自然度高，支持多种情绪（happy/sad/angry/fearful/disgusted/surprised/calm/fluent/whisper），"
                "\n"
                "支持语速调节（0.5x-2.0x），支持音色克隆，支持多种语言。"
                "\n"
                "支持模型版本：speech-2.8-hd, speech-2.8-turbo, speech-2.6-hd, speech-2.6-turbo 等。"
                "\n"
                "适用场景：有声内容制作、配音、辅助阅读、语音播报。"
                "\n"
                "不适用场景：需要实时流式输出的场景（使用异步接口有延迟）。"
                "\n"
                "情绪参数：happy（高兴）、sad（悲伤）、angry（愤怒）、fearful（害怕）、"
                "\n"
                "disgusted（厌恶）、surprised（惊讶）、calm（中性）、fluent（生动）、whisper（低语）。"
                "\n"
                "语速参数：0.5（慢速）到 2.0（快速），默认 1.0。"
                "\n"
            ),
            requirements=["minimax_apikey"],
            mode="programmatic",
            mapper_func=_minimax_t2a_speech_mapper,
            inputs={
                "prompt": PortSpec(type=PortType.TEXT, required=True, title="合成文本", description="待合成文本"),
                "voice_id": PortSpec(
                    type=PortType.STRING, title="预置音色", description="预置音色 ID,留空使用默认 audiobook_male_1"
                ),
                "reference_audio": PortSpec(type=PortType.AUDIO, title="参考音频", description="参考音频,用于音色克隆"),
                "mood": PortSpec(
                    type=PortType.STRING,
                    title="情绪",
                    description="情绪:happy/sad/angry/fearful/disgusted/surprised/calm/fluent/whisper",
                ),
                "speed": PortSpec(
                    type=PortType.NUMBER,
                    default=1.0,
                    minimum=0.5,
                    maximum=2.0,
                    title="语速",
                    description="语速,0.5~2.0",
                ),
                "language_boost": PortSpec(
                    type=PortType.STRING,
                    default="auto",
                    title="语言增强",
                    description="语言增强:auto / zh / en / ja ...",
                ),
                "params": PortSpec(
                    type=PortType.STRING, title="扩展参数", description="预留扩展,可指定 model=speech-2.8-hd 等"
                ),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["speech"],
                mode="async_poll",
                priority=60,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输入文本字符数计费(3.5 元/万字符 = 350 积分/万字符)。"""
        return estimate_minimax_t2a_points(request.prompt)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(空文本) ~ 最大(300 字符上限)。"""
        return (
            estimate_minimax_t2a_points(""),
            estimate_minimax_t2a_points("你" * 300),
        )


def _fish_tts_knowledge(spec: FishModelSpec) -> str:
    bill = "本插件不计积分(上游免费档)。" if spec.free else "按输入文本 UTF-8 字节计费。"
    return (
        f"{spec.display_name}({spec.name}) —— 配音 / 口播 / 有声内容。"
        "\n"
        "优势:多语言,韵律自然;情绪块 <<EMO: 情绪词>> 可句中定位与叠加,"
        "如 今天<<EMO: 开心>>天气真好。字面 []/【】 当普通文本。"
        "\n"
        "传入参考音频即自动克隆音色(内容去重、持久复用)。"
        "单个角色:把音频放进 reference_audio,正文里不要写任何标记。"
        "多个角色写同一段正文:每条音频放进 reference_audios,"
        "正文里用 <|speaker:0|> 对应 reference_audios[0]、<|speaker:1|> 对应 reference_audios[1],"
        "再次出现同一编号仍是这个人;reference_audios 至少两条时整组生效,"
        "只有一条时与 reference_audio 等价。"
        "\n"
        f"{bill}"
        "\n"
        "适用场景:配音、口播、有声内容、多语言朗读、角色对白。"
        "\n"
        "不适用场景:音乐生成。"
        "\n"
    )


class _FishTtsCtor(Protocol):
    """档位类自己组 NodeDef。type[FishTtsModel] 会要求调用方传入 node。"""

    def __call__(self) -> FishTtsModel: ...


def _build_fish_tts(spec: FishModelSpec) -> _FishTtsCtor:
    """同一 TTS 执行链按上游 model 拆成可独立启停的模型。"""
    engine = spec.name
    is_free = spec.free

    class Def(FishTtsModel):
        def __init__(self) -> None:
            super().__init__(self.node_def())

        @staticmethod
        def node_def() -> NodeDef:
            return NodeDef(
                name=engine,
                display_name=spec.display_name,
                task_type=TaskType("speech"),
                backend="fishaudio",
                backend_model=engine,
                point_cost=0 if is_free else 2,
                description=spec.description,
                knowledge_content=_fish_tts_knowledge(spec),
                requirements=["fishaudio_apikey"],
                mode="programmatic",
                mapper_func=_fishaudio_tts_mapper_for(engine),
                inputs={
                    "prompt": PortSpec(
                        type=PortType.TEXT,
                        required=True,
                        title="合成文本",
                        description=(
                            "待合成文本。用情绪块 <<EMO: 情绪词>> 在句中定位情绪"
                            "（如 你好<<EMO: 低语>>我想你了）；正文里字面的 []/【】 当普通文本。"
                        ),
                    ),
                    "reference_audio": PortSpec(
                        type=PortType.AUDIO,
                        title="参考音频",
                        description=(
                            "传入即自动克隆音色并持久复用，无需显式克隆。"
                            "多角色对白改用 reference_audios（见 knowledge）。"
                        ),
                    ),
                    # 多角色对白专用。只在 inputs 里声明、不在 dispatch 强校验,
                    # mapper 的 _speaker_clips 取 >=2 条时整组生效,单条等价 reference_audio。
                    "reference_audios": PortSpec(
                        type=PortType.AUDIO,
                        title="多角色参考音频",
                        description=(
                            "多角色对白音色,下标对应正文里的 <|speaker:N|>,至少两条时整组生效;"
                            "单条等价 reference_audio。同一编号重复出现仍是同一个人。"
                        ),
                    ),
                    "mood": PortSpec(
                        type=PortType.STRING,
                        title="情绪",
                        description="整句情绪/语气，自动注入句首；句中定位请用情绪块 <<EMO: 情绪>>",
                        values=["开心", "悲伤", "愤怒", "惊讶", "平静", "兴奋", "低语", "哭腔", "大笑", "着急"],
                    ),
                    "speed": PortSpec(
                        type=PortType.NUMBER,
                        default=1.0,
                        minimum=0.5,
                        maximum=2.0,
                        title="语速",
                        description="语速，0.5~2.0",
                    ),
                    "params": PortSpec(type=PortType.STRING, title="扩展参数", description="预留扩展"),
                },
                outputs={
                    "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
                },
                capabilities=CapabilityManifest(
                    supported_tasks=["speech"],
                    mode="sync",
                    priority=spec.priority,
                ),
            )

        def estimate_cost(self, request: GenerationRequest) -> int:
            if is_free:
                return 0
            return estimate_fish_tts_points(request.prompt)

        def point_range(self) -> tuple[int, int]:
            if is_free:
                return (0, 0)
            return (
                estimate_fish_tts_points(""),
                estimate_fish_tts_points("你" * 300),
            )

    Def.__name__ = "FishTts_" + engine.replace(".", "_").replace("-", "_")
    Def.__qualname__ = Def.__name__
    return Def


_FISH_TTS_BY_NAME: dict[str, _FishTtsCtor] = {spec.name: _build_fish_tts(spec) for spec in FISH_TTS_MODELS}
FishTtsDef = _FISH_TTS_BY_NAME["s2.1-pro"]
FishTtsFreeDef = _FISH_TTS_BY_NAME["s2.1-pro-free"]


def _usage_blob(usage: dict) -> dict[str, object]:
    blob: dict[str, object] = {}
    for key, value in usage.items():
        if isinstance(key, str):
            blob[key] = value
    return blob


class GeminiTtsDef(GeminiTtsModel):
    """Gemini 3.8 Flash-Lite TTS。勾选名 gemini_tts,凭证与生图共用。"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="gemini_tts",
            display_name="Gemini 3.8 Flash-Lite TTS",
            task_type=TaskType("speech"),
            backend="gemini_speech",
            backend_model=GEMINI_TTS_UPSTREAM_MODEL,
            point_cost=1,
            description="单说话人预置音色,输出 WAV",
            knowledge_content=(
                "Gemini 3.8 Flash-Lite TTS(gemini_tts) —— 单说话人朗读。"
                "\n"
                "30 个预置音色,默认 Kore。风格写在 mood,不会被读出来。"
                "正文里的尖括号事件保留。不支持语速、参考音频、多说话人。"
                "\n"
                "输出 WAV。2026-12-31 及以前按 Standard:输入文本 50 积分/百万 token,音频 600。"
                "2027-01-01 起输入 100、音频 1200。1 美元 = 100 积分,向上取整,最少 1。"
                "预扣按字数估算时长;成功后按 usage 实扣。"
                "\n"
                "适用场景:配音、口播、有声内容。"
                "\n"
                "不适用场景:音乐生成、多说话人、音色设计。"
                "\n"
            ),
            requirements=["gemini_image_apikey"],
            mode="programmatic",
            mapper_func=_gemini_tts_mapper_for(GEMINI_TTS_UPSTREAM_MODEL),
            inputs={
                "prompt": PortSpec(
                    type=PortType.TEXT,
                    required=True,
                    title="合成文本",
                    description="待合成文本,最长 4000 字",
                ),
                "voice_id": PortSpec(
                    type=PortType.ENUM,
                    values=list(GEMINI_TTS_VOICES),
                    default=GEMINI_TTS_DEFAULT_VOICE,
                    required=False,
                    title="预置音色",
                    description="留空使用 Kore",
                ),
                "mood": PortSpec(
                    type=PortType.STRING,
                    required=False,
                    title="风格",
                    description="自然语言风格,不写入正文",
                ),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["speech"],
                mode="sync",
                priority=66,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        return estimate_gemini_tts_points(request.prompt)

    def settle_cost(self, request: GenerationRequest, usage: dict) -> int | None:
        return settle_gemini_tts_points(_usage_blob(usage))

    def point_range(self) -> tuple[int, int]:
        return (
            estimate_gemini_tts_points(""),
            estimate_gemini_tts_points("你" * self.max_text_length),
        )


@dataclass(frozen=True)
class _QwenTtsSpec:
    name: str
    display_name: str
    priority: int
    description: str
    bill: str


_QWEN_TTS_SPECS: tuple[_QwenTtsSpec, ...] = (
    _QwenTtsSpec(
        QWEN_TTS_31,
        "Qwen-Audio 3.1 TTS Flash",
        64,
        "系统音色,按 token 计费",
        "按 token 计费。北京输入 150 积分/百万 token、输出 1200;"
        "国际站输入 167.7、输出 1363.6,向上取整。"
        "预扣按字数估算时长,成功后有 usage 才改实扣。",
    ),
    _QwenTtsSpec(
        QWEN_TTS_PLUS,
        "Qwen-Audio TTS Plus",
        63,
        "系统音色,按字计费",
        "按输入字符计费,输出不计。北京 140 积分/万字,国际站 1.49884 元/万字。汉字计 2。",
    ),
    _QwenTtsSpec(
        QWEN_TTS_FLASH,
        "Qwen-Audio TTS Flash",
        62,
        "系统音色,按字计费",
        "按输入字符计费,输出不计。北京 100 积分/万字,国际站 1.12413 元/万字。汉字计 2。",
    ),
)


class _QwenTtsCtor(Protocol):
    def __call__(self) -> QwenTtsModel: ...


def _qwen_tts_knowledge(spec: _QwenTtsSpec) -> str:
    return (
        f"{spec.display_name}({spec.name}) —— 单说话人朗读。"
        "\n"
        "只用该模型自己的系统音色。风格写在 mood,作为 instruction,不进正文;"
        "汉字按 2 计,上限 100。语速 0.5~2.0。"
        "\n"
        f"{spec.bill}"
        "\n"
        "输出 MP3。地区跟当前 DashScope 根地址:主机名含 dashscope-intl 用国际站价格,否则北京。"
        "已保存的启用列表不会自动勾上这个模型。"
        "\n"
        "适用场景:配音、口播、有声内容。"
        "\n"
        "不适用场景:音乐生成、音色克隆。"
        "\n"
    )


def _build_qwen_tts(spec: _QwenTtsSpec) -> _QwenTtsCtor:
    engine = spec.name
    voices = QWEN_VOICES_BY_MODEL[engine]
    default_voice = QWEN_DEFAULT_VOICE_BY_MODEL[engine]

    class Def(QwenTtsModel):
        def __init__(self) -> None:
            super().__init__(self.node_def())

        @staticmethod
        def node_def() -> NodeDef:
            return NodeDef(
                name=engine,
                display_name=spec.display_name,
                task_type=TaskType("speech"),
                backend="dashscope_tts",
                backend_model=engine,
                point_cost=1,
                description=spec.description,
                knowledge_content=_qwen_tts_knowledge(spec),
                requirements=["HappyHorse_apikey_dashscope"],
                mode="programmatic",
                mapper_func=_qwen_tts_mapper_for(engine, voices, default_voice),
                inputs={
                    "prompt": PortSpec(
                        type=PortType.TEXT,
                        required=True,
                        title="合成文本",
                        description="待合成文本,单次最长 20000 字",
                    ),
                    "voice_id": PortSpec(
                        type=PortType.ENUM,
                        values=list(voices),
                        default=default_voice,
                        required=False,
                        title="系统音色",
                        description=f"留空使用 {default_voice}。不能换成另一个模型的音色",
                    ),
                    "mood": PortSpec(
                        type=PortType.STRING,
                        required=False,
                        title="风格",
                        description="自然语言风格,不写入正文。汉字按 2 计,上限 100",
                    ),
                    "speed": PortSpec(
                        type=PortType.NUMBER,
                        default=1.0,
                        minimum=0.5,
                        maximum=2.0,
                        title="语速",
                        description="语速,0.5~2.0",
                    ),
                },
                outputs={
                    "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
                },
                capabilities=CapabilityManifest(
                    supported_tasks=["speech"],
                    mode="sync",
                    priority=spec.priority,
                ),
            )

        def estimate_cost(self, request: GenerationRequest) -> int:
            return estimate_qwen_tts_points(engine, request.prompt)

        def settle_cost(self, request: GenerationRequest, usage: dict) -> int | None:
            return settle_qwen_tts_points(engine, _usage_blob(usage))

        def point_range(self) -> tuple[int, int]:
            return (
                estimate_qwen_tts_points(engine, ""),
                estimate_qwen_tts_points(engine, "你" * self.max_text_length),
            )

    Def.__name__ = "QwenTts_" + engine.replace(".", "_").replace("-", "_")
    Def.__qualname__ = Def.__name__
    return Def


_QWEN_TTS_BY_NAME: dict[str, _QwenTtsCtor] = {spec.name: _build_qwen_tts(spec) for spec in _QWEN_TTS_SPECS}

ALL_MODELS = [
    IndexTTS2Def,
    IndexTTS25Def,
    MimoTtsDef,
    MinimaxT2aSpeechDef,
    GeminiTtsDef,
    *_QWEN_TTS_BY_NAME.values(),
    *_FISH_TTS_BY_NAME.values(),
]
