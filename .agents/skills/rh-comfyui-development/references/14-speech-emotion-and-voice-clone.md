# 十四、语音情绪体系与自动音色克隆

本章讲两件在语音模态里通用、且新增语音模型时最容易踩坑的机制:

1. **情绪归一化在基类统一处理** —— 各模型只声明"情绪风格",嵌入还是剥离由基类决定;
2. **参考音频 → 自动音色克隆 + 持久去重** —— 用户传参考音频即隐式克隆,同一段音频跨重启复用。

以 `fish_tts`(Fish Audio S2 系列 TTS)为落地样例。

## 14.1 为什么情绪要放在基类

不同 TTS 上游表达情绪的方式是**互斥**的:

| 上游风格 | 情绪怎么给 | 正文里的内联 `[标签]` 应当 |
|---|---|---|
| 内联标签 | 写进正文 `[happy] ...`,支持句中定位与叠加 | **保留**(上游原生解析) |
| 自然语言 | 一条独立的自由文本指令 | **剥离**(否则被当文字读出来) |
| 固定枚举 | 必须落在有限集合内 | **剥离**,并把情绪收敛到枚举 |
| 无 | 不吃情绪 | **剥离** |

如果让每个模型的 mapper 各写一遍"要不要剥离 / 怎么翻译",既重复又容易漏。所以统一上移到
模态基类 `DigitalHumanSpeechBase`:模型只声明一个 `emotion_style`,`normalize()` 按风格把
`(prompt, mood)` 整形成上游能直接消费的形态,mapper 只读归一后的字段。

## 14.2 EmotionStyle 与基类钩子

- 枚举与纯函数在 `core/base/emotion.py`(无外部依赖,可单测):
  - `translate_inline_zh_tags(text)`:正文里 `[中文情绪]` → `[english]`,普通括注(如 `(苹果)`)不动;
  - `strip_inline_tags(text) -> (clean, stripped)`:剥离"像情绪/语气标记"的 `[..]/(..)`,返回被剥内容;
  - `to_inline_tag(mood)`:结构化情绪包成句首标签(已带括号原样,中文→英文);
  - `to_enum_emotion(mood, extra, allowed)`:结构化情绪或剥出标记收敛到模型枚举,不命中返回 `None`。
- 判定"像标记"很保守:命中中文情绪表、或纯英文短词才剥;中文非情绪括注(`(苹果)`/`[重要]`)一律保留。

基类 `DigitalHumanSpeechBase`(`core/base/speech.py`):

```python
emotion_style: EmotionStyle = EmotionStyle.NATURAL_LANGUAGE  # 默认自然语言
emotion_enum: list[str] = []                                 # 仅 ENUM 风格用

def normalize(self, request):                 # run() 生命周期第 2 步的钩子
    request = super().normalize(request)
    return self._apply_emotion(request)       # 按 emotion_style 整形 prompt / mood
```

`normalize()` 是 `AIGCGenerationBase.run()` 模板方法里既有的钩子(校验后、执行前),**三入口
(命令 / AI / HTTP)都会经过**,所以情绪整形对所有入口一致生效,mapper 不必重复处理。
`supports_mood=False` 的模型自动按"无"处理(剥离并清空 mood)。

## 14.3 各模型只声明风格(新增模型的最小改动)

`models/speech/overrides.py` 里每个模型只挑一个风格:

```python
class IndexTTS2Model(SpeechPipelineModel):
    emotion_style = EmotionStyle.NATURAL_LANGUAGE   # 情绪走独立自由文本

class MinimaxSpeechModel(SpeechPipelineModel):
    emotion_style = EmotionStyle.ENUM               # 收敛到固定枚举
    emotion_enum = ["happy", "sad", "angry", "fearful", "disgusted",
                    "surprised", "calm", "fluent", "whisper"]

class FishTtsModel(SpeechPipelineModel):
    emotion_style = EmotionStyle.INLINE_BRACKET      # 正文内联 [tag],可句中定位/叠加
```

`defs.py` 的模型类继承对应基类即可(如 `class FishTtsDef(FishTtsModel)`)。**新增语音模型时不用
再碰情绪逻辑**,选一个 `emotion_style`(必要时给 `emotion_enum`)就继承到位。

### 归一化行为速查(已单测,见 `tests/test_speech_emotion.py`)

| 输入 prompt / mood | INLINE_BRACKET(fish) | ENUM(minimax) | NATURAL_LANGUAGE(mimo) |
|---|---|---|---|
| `今天 [高兴] 天气真好` | `今天 [happy] 天气真好` / — | `今天 天气真好` / `happy` | `今天 天气真好` / `高兴` |
| prompt=`你好`, mood=`开心` | `[happy] 你好` / — | `你好` / `happy` | `你好` / `开心` |
| prompt=`你好`, mood=`随便描述` | `[随便描述] 你好` / — | `你好` / `None`(丢弃) | `你好` / `随便描述` |
| `我买了(苹果)三个` | 原样(普通括注不动) | 原样 | 原样 |

要点:内联标签只对 `INLINE_BRACKET` 保留;其余风格剥离,**避免把 `[..]` 当文字念出来**。
枚举收敛把中文/自由描述归一后过滤,枚举外自动丢弃(而不是塞给上游被静默忽略)。

## 14.4 自动音色克隆 + 持久去重

诉求:用户传参考音频就**隐式**克隆(无显式克隆命令),且同一段参考音频反复提交时不重复克隆。

- 持久映射表 `RHVoiceCloneCache`(`utils/database/models.py`):`(provider, audio_hash) → 音色 id`。
  - **按参考音频内容哈希全局去重**:同音频→同音色,跨用户、跨重启复用(克隆是纯内容派生,
    `created_by` 仅审计,不做隔离);
  - `get_voice_id()` / `remember()` 均 `@with_session`,并发下"存在即跳过"避免重复行。
- 后端 `utils/backends/fishaudio/`(照 `mimo`/`minimax` Adapter 范式):
  - `create_voice_model(audio, title)`:快速克隆一个私有音色(不进公共库),返回可复用的音色 id;
  - `tts(text, reference_id, model, speed)`:合成;`reference_id` 为空时用档位内置默认音色。
- mapper `utils/mappers/fishaudio_speech.py`:`hash → 查表命中即复用;未命中克隆一次 → 写表`。
  克隆失败**直接抛错、不回退随机音色**(与 minimax 一致,dispatch 会退款)。

> 后端"model"有两层含义,别混:配置里的 `FishAudio_Model`(合成引擎档位,如 `s2.1-pro-free`)
> 与克隆得到的音色 id(`reference_id`)是两个独立概念。

## 14.5 配置与档位

`rh_config/service_config.py`:

| 键 | 用途 |
|---|---|
| `FishAudio_apikey` | Fish Audio 官方 API Key |
| `FishAudio_Model` | 合成档位,默认免费档 `s2.1-pro-free`;可切 `s2.1-pro` / `s2-pro` / `s1`(需对应权限) |

档位也可被单次请求经 `params.model` 覆盖(请求 > 配置默认)。仅对接官方公开端点(不额外暴露地址配置)。

## 14.6 三入口如何用情绪(含群聊)

- **命令(群聊)**:模型名走前缀匹配,直接 `fish 你好` 即命中 `fish_tts`(无需额外别名);
  `[情绪]` 由命令层 `parse_mood_from_prompt` 先提取进 `mood`,附带音频→`reference_audio` 触发克隆。
- **AI Agent**:经 `to_ai` 桥接复用同一命令,`knowledge_content` 里写清了内联标签用法供选型。
- **HTTP 入口**:上游调用方按模型 `input_schema` 组织表单 —— `mood` 为自由文本端口(带 `values`
  仅作建议项,不强制枚举),`reference_audio` 为音频输入端口(连线即触发自动克隆)。

> 参考音频端口(`reference_audio`)是模态级一等端口:上游调用方据 `input_schema` 中是否存在该端口
> 决定是否暴露音频输入口。这也是**允许其他插件接入**时对齐能力面的依据。

## 14.7 新增一个语音模型的清单

1. `utils/backends/<家>/` 写 Adapter(通信细节);`utils/backends/__init__.py` 注册;
2. `utils/mappers/<家>_speech.py` 写 mapper(只读归一后的 `prompt`/`mood`);
3. `models/speech/overrides.py` 声明 `emotion_style`(必要时 `emotion_enum`);
4. `models/speech/defs.py` 加定义类(端口含 `prompt` + 可选 `reference_audio`/`mood`/`speed`)→ `ALL_MODELS`;
5. 需要克隆就复用 `RHVoiceCloneCache`(换 `provider` 值即可);配置键加进 `service_config.py`;
6. `python -m pytest tests/ -q` + `ruff check`。
