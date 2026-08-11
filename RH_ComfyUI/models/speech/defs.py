"""models/speech/defs.py — 编程式模型定义(自 pipelines YAML 迁移,2026-07)

每个模型一个类:node_def() 用代码声明身份/端口/映射,执行链沿用
桥接层(NodeDef + Adapter)。修改参数面直接改本文件,改完跑
`python -m pytest tests/ -q` 验证。
"""

from __future__ import annotations

from ..bridge import SpeechPipelineModel
from .overrides import FishTtsModel, IndexTTS2Model, MinimaxSpeechModel
from ...utils.core.types import PortSpec, PortType, CapabilityManifest
from ...utils.core.request import TaskType, GenerationRequest
from ...utils.core.pipeline import NodeDef
from ...utils.mappers.speech import index_tts2_mapper as _index_tts2_mapper
from ...utils.mappers.mimo_speech import mimo_tts_mapper as _mimo_tts_mapper
from ...utils.mappers.extra_billing import estimate_mimo_tts_points, estimate_minimax_t2a_points
from ...utils.mappers.minimax_speech import minimax_t2a_speech_mapper as _minimax_t2a_speech_mapper
from ...utils.mappers.speech_billing import estimate_fish_tts_points, estimate_index_tts2_points
from ...utils.mappers.fishaudio_speech import fishaudio_tts_mapper as _fishaudio_tts_mapper


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
            description="语音合成模型，语音自然，中文发音准确",
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
            description="XiaoMi MiMo-V2.5-TTS 系列语音合成，支持预置音色、音色设计、音色复刻，风格控制丰富",
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
            description="MiniMax 异步语音合成模型，支持多种情绪、语速调节、音色克隆，语音自然度高",
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


class FishTtsDef(FishTtsModel):
    """Fish Audio S2 语音合成 — 自动音色克隆 + 内联情绪"""

    def __init__(self) -> None:
        super().__init__(self.node_def())

    @staticmethod
    def node_def() -> NodeDef:
        return NodeDef(
            name="fish_tts",
            display_name="Fish Audio S2",
            task_type=TaskType("speech"),
            backend="fishaudio",
            point_cost=2,
            description="Fish Audio S2 语音合成，多语言、韵律自然，情绪可句中定位，支持自动音色克隆；配音/口播首选",
            knowledge_content=(
                "Fish Audio S2 系列语音合成模型 —— 配音 / 口播 / 有声内容的**首选**模型。"
                "\n"
                "优势：多语言支持，韵律自然，情绪表达细粒度可控（可句中定位与叠加），"
                "\n"
                "传入参考音频即自动克隆音色（内容去重、持久复用，无需显式克隆步骤）。"
                "\n"
                "情绪用法（重要）：用情绪块 <<EMO: 情绪词>> 在句中定位情绪，"
                "\n"
                "如 今天<<EMO: 开心>>天气真好 / 你好<<EMO: 低语>>我想你了，可连续叠加"
                "\n"
                "如 <<EMO: 悲伤>><<EMO: 低语>>；情绪词支持中文（开心/低语/悲伤/大笑…）。"
                "\n"
                "也可用『情绪』参数或文本开头 [情绪] 指定整句情绪。"
                "\n"
                "注意：只有情绪块 <<EMO: …>> 才被识别为情绪；正文里字面的 []/【】 当普通文本。"
                "\n"
                "模型档位可配置（默认免费档 s2.1-pro-free，可切换更高档位）。"
                "\n"
                "适用场景：配音、口播、有声内容、多语言朗读、角色对白。"
                "\n"
                "不适用场景：音乐生成。"
                "\n"
            ),
            requirements=["fishaudio_apikey"],
            mode="programmatic",
            mapper_func=_fishaudio_tts_mapper,
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
                    description="传入即自动克隆音色并持久复用，无需显式克隆",
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
                "params": PortSpec(type=PortType.STRING, title="扩展参数", description="预留扩展，可指定 model 档位等"),
            },
            outputs={
                "audio": PortSpec(type=PortType.OUTPUT_AUDIO, description="生成的语音"),
            },
            capabilities=CapabilityManifest(
                supported_tasks=["speech"],
                mode="sync",
                # 配音/口播首选:优先级高于其它 TTS(未配置 key 时 check_available 自动让路)
                priority=85,
            ),
        )

    def estimate_cost(self, request: GenerationRequest) -> int:
        """动态计费:按输入文本 UTF-8 字节长度计费(15 美元 / M bytes)。"""
        return estimate_fish_tts_points(request.prompt)

    def point_range(self) -> tuple[int, int]:
        """积分范围:最小(空文本) ~ 最大(300 字符上限)。"""
        return (
            estimate_fish_tts_points(""),
            estimate_fish_tts_points("你" * 300),
        )


ALL_MODELS = [IndexTTS2Def, MimoTtsDef, MinimaxT2aSpeechDef, FishTtsDef]
