# backends/gemini_omni — Gemini Omni 视频(Interactions API)

挂在 `gemini_omni12` 上:`GeminiOmniChannel`(通道名 `gemini-omni`,`weight=2`),
`supports_remote_cancel=True`(background interaction 可 `interactions.cancel`)。

| 文件 | 职责 |
|---|---|
| `api.py` | `GeminiOmniAPI(GeminiImageAPI)`:interactions create + 自有轮询循环、`_find_video` 从 `steps[].content[{type:"video"}]` 取 base64/外链、`_interaction_usage` 提取 token 用量;`resume_video` 供 resume_poll |
| `channel.py` | ProviderChannel 包装:启用门控(「启用的 Gemini 模型」勾选 `gemini_omni12`)、凭证守卫、错误翻译 |

凭证/Vertex/中转 base_url 与生图**共用** `Gemini_Image_*` 配置(继承
`GeminiImageAPI._build_client`),不需要新配置键。请求组装与
response_format 在 `utils/mappers/gemini_omni.py`,计费在
`utils/mappers/gemini_omni_billing.py`(视频输出 $12/1M tok,$1.5/M 输入,
USD×7×100 → 积分)。
