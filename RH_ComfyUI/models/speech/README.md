# models/speech — 数字人语音编程式模型

| 类 | 能力面 |
|---|---|
| `IndexTTS2Model` | 自然中文 TTS;`reference_audio` 参考音频音色克隆(一等端口,调用方可连音频节点);情绪指令 |

参考音频数据流(蓝图 §10):调用方音频节点 → HTTP 请求 `reference_audio`
(MediaRef)→ `GenerationRequest` → 模型 `input_schema()` 声明的端口 →
Adapter/workflow 注入,不再硬编码在 workflow mapper 里。

## 维护须知

- 新 TTS 模型若支持音色克隆,声明 `supports_voice_clone = True` 并在
  `input_schema()` 暴露 `reference_audio` 端口,调用方即自动出现连线口;
- 无参考音频时的回落行为(内置音色 or 报错)由 `has_default_voice` 决定。
