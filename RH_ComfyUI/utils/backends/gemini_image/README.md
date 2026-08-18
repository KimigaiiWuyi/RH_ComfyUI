# backends/gemini_image — Gemini 生图通道(google-genai SDK)

**不是 Adapter**(不进 backend_registry)。`GeminiImageChannel` 是 banana2
(Nano Banana 2)的一路供应商通道,走官方 google-genai SDK 的
`models.generate_content`,不手拼 REST/鉴权。不要用 Interactions:
它会把模型改写成 `…-agent`,参考图会被 400 拒。

| 文件 | 职责 |
|---|---|
| `api.py` | `GeminiImageAPI`:配置全 `@property` 直读(热更新);双模构建 Client;`generate_content` 取图 |
| `channel.py` | `GeminiImageChannel`:守卫用 `is_configured()`(Vertex 合法无 key);错误翻译成可重试 `ChannelError` |

双模(显式开关 `Gemini_Image_Use_Vertex`,SDK 限制 project 与 api_key 互斥):
- 关(默认)= AI Studio → `Client(api_key=Gemini_Image_apikey)`;
- 开 = VertexAI → `Client(vertexai=True, project, location)`,鉴权走 ADC 或
  `Gemini_Image_SA_File` 服务账号 JSON,忽略 api_key。

维护须知:
- **通道内一切凭证守卫用 `is_configured()`,不要按 `api_key` 判**(Vertex 无 key);
- 服务器直连不到 Google 时填 `Gemini_Image_BaseURL`(中转地址),SDK 经
  `http_options.base_url` 改道,并在其后拼 `/v1beta/...`(`api_version` 不变);
  **仅 AI Studio 模式生效**(Vertex 有自己的端点体系,套上去会打歪)。留空直连官方;
- 新请求从 `generate_content` 的 `candidates[0].content.parts[].inline_data` 取图;
  旧 interaction 续跑仍走 `_find_image`(outputs / steps);
- 详细文档见 `.agents/skills/rh-comfyui-development/references/12-provider-channels-and-gemini.md`;
- 测试:`tests/test_gemini_image.py`。
