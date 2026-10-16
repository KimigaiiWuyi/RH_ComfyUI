# backends/gemini_speech — Gemini 3.8 Flash-Lite TTS

单说话人语音。凭证与生图共用 `Gemini_Image_*` / Vertex,勾选名是 `gemini_tts`。
不要把请求送进 `GeminiImageChannel`：那边的 `generate_content` 写死 `response_modalities=IMAGE`。

| 文件 | 职责 |
|---|---|
| `voices.py` | 30 个预置音色,默认 Kore;上游模型 id |
| `api.py` | 复用 `GeminiImageAPI._build_client()`,另发 AUDIO + `speech_config` |
| `executor.py` | Adapter `gemini_speech`。包初始化不 import 本目录的客户端 |

输出是上游 unary WAV,不再包一层 PCM。`mood` 进 `speech_metadata.style`,不进正文。
无远程取消,也不做 resume。
