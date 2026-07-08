# 十二、供应商通道 / Gemini 生图 / 能力一致性 — 注意点与易踩坑

> 本章汇总 2026-07 "Seedance 单层负载均衡 + 多供应商通道 + Gemini 原生接入"
> 一轮重构的关键决策、红线与踩坑。改这块前先读本章。

## 1. 通道架构不变量(单层负载均衡后)

2026-07 起 **每家供应商 = 一个 `ProviderChannel`**,由通用
`LoadBalancer`(core/routing/balancer.py)统一排序/熔断/故障切换。Seedance /
Gemini 已**不是** Adapter(不在 `backend_registry` 里)。

- **AdapterChannel 必须翻译异常**(models/bridge.py):`adapter.execute()` 抛的
  裸 `RuntimeError` 等要在 `AdapterChannel.invoke` 里翻成
  `ChannelError(retryable=True)`(`GenerationError` 子类原样抛),否则一模型多
  通道时**不会 failover**,第一路失败就整单挂。踩坑现场:banana2 走网关 503
  却不切 Gemini。
- **/models 可用性看模型、不看后端**:`rh_models/api.py::_build_entry` 用
  `model.check_available()`(遍历 `channel_bindings()`),**不能**再用
  `backend_registry.get(node.backend)` —— Seedance/Gemini 无 Adapter,否则
  报 `available=false, "后端 seedance 未注册"`,即使网关 key 已配。
- **跨插件扩展点**:`channel_registry.register_binding(model_name, channel,
  vendor_model=)`(从 `RH_ComfyUI.core` 顶层公开)。所有桥接模型
  (`_PipelineBackedMixin`)自动并入外部通道。**RH_ComfyUI 代码/文档不得出现
  具体外部插件名或其来源暗示,只留通用接口。
- 熔断只由 `ChannelError(retryable=True)` 触发;`AllChannelsFailedError` 现继承
  最后一路 `cause.user_message`,保住供应商干净失败文案。

## 2. 消费统计维度

- `backend_provider` = 通道名(`output.metadata["channel"]`,run() 落);
- `backend_key_prefix` = key 前 6 位,来自 `ProviderChannel.audit_key_prefix()`
  (run() 落 metadata,recorder 透传,DB 列已加)。含密钥的通道要记前缀就覆盖
  `audit_key_prefix`。
- 负载均衡策略/阈值键:`Load_Balance_Mode` / `Failure_Threshold`(SERVICE_CONFIG,
  旧 `Seedance_Load_Balance` / `Seedance_Failure_Threshold` 已迁移)。

## 3. Gemini 生图(banana2 = 原生 Nano Banana 2)

- **走官方 `google-genai` SDK 的 `client.aio.interactions.create`**,不要手拼
  REST/URL/鉴权(手拼反复 404:global 主机、Vertex 路径、Bearer 都易错)。
- **双模互斥(SDK 硬约束)**:`Client(vertexai=True, project=…, api_key=…)` 会抛
  "Project/location and API key are mutually exclusive"。故用**显式开关**
  `Gemini_Image_Use_Vertex`:
  - 关(默认)= AI Studio → `Client(api_key=Gemini_Image_apikey)`;
  - 开 = VertexAI → `Client(vertexai=True, project, location)`,鉴权走 ADC 或
    `Gemini_Image_SA_File` 服务账号 JSON,**忽略 api_key**。
  - **别再用"填了 project_id 就走 Vertex"推断** —— 用户填了 key+project 会被迫
    走 Vertex、报 ADC 缺失(踩坑现场)。
- **图片在 `steps` 里,不在 `outputs`**:interactions 响应 `outputs` 常为空,图在
  `steps[*].content[*]` 的 `{type:"image", data:<base64>}`(**inline base64,不是
  uri**)。`usage.output_tokens_by_modality` 里有 image 即已出图。`steps` 是 SDK
  未声明的 extra 字段,取值要 `getattr` + `model_extra` 兜底,内层是原始 dict。
  提取器见 `gemini_image/api.py::_find_image`(先 outputs 再 steps,兼容 data/uri)。
- **banana2 独立于 gpt-image-2**:`banana2.backend="gemini-image"`,
  `Banana2Def.channel_bindings()` 只挂 `GeminiImageChannel`;请求 Nano Banana 2
  **不经过** gpt-image-2(OpenAI 兼容)后端。日志里 `[GPT-Image2]` 是**后端名**
  不是模型名 —— 别被误导以为路由错了。
- **schema 用 ratio + image_size,不用宽高**:Gemini 只吃 `aspect_ratio`(枚举)+
  `image_size`(512/1K/2K/4K),不吃像素宽高。banana2 的 input 端口是 `ratio` /
  `image_size`(`image_size` 走 params 透传给 mapper)。

## 4. input_schema 必须与模型能力一致(agent / 前端据此判参数)

`/RH_ComfyUI/models` 的 `input_schema` 是**机器可读的能力契约**:agent(画布)
按它决定给哪个模型传什么参数,前端按它渲染表单。规则:

- 有 `images` 端口 ⇒ 支持传参考图;`max_items` ⇒ 上限;
- **纯文生图 / 纯文本模型无 `images` 端口** → 桥接层推出 `supports_edit=False`,
  `ImageGenerationBase.validate` 会拒图(`"纯文生图模型,不接受参考图"`);
- `ModelEntry` 另有显式 `accepts_images` / `max_input_images` 便于直接判定。

**踩坑**:`SeedanceVideoModel.__init__` **强制** `supported_shapes = 全 4 形态`
(文生/图生/首尾帧/多模态),但 `seedance15_pro` / `seedance2_fast` 曾漏声明
`images`/`video_refs`/`audio_refs`/`frame_mode` 端口 → schema 藏了能力,agent/前端
以为它们只能文生。**改模型 supported_shapes / max_reference_total 时,务必同步
input 端口**。现全部 Seedance 变体端口已对齐。

| 模型 | 参考输入端口 | 能力 |
|---|---|---|
| anima / minimax_image01 / qwen_2512 | 无 | 纯文生图(拒图) |
| banana2 | images(≤14) | 原生 Gemini,图生/多图参考 |
| banana_pro / gpt-image-2 / qwen_2511 | images | 图生/编辑 |
| seedance*(全变体)| images/video_refs/audio_refs | 多模态视频 |
| wan2.2_videogen | images(≤2) | 首尾帧 |
| ace_step1.5 | 无(negative_prompt=歌词) | 文生音乐 |
| IndexTTS2 / mimo_tts / minimax_t2a_speech | reference_audio | 音色复刻 |

## 5. 计费 / 退款(canvas / http 入口)

- http 入口用 `ExternalPrepaidPolicy`:**引擎只记账不扣费**,扣费/退款由调用方
  (`canvas_backend/generate_api.py`)负责。生成失败时 canvas 侧
  `_run_generation` 的 `except` 会 `refund_rh` 退还。
- **两笔独立积分池**:canvas 扣的是 `RHBind`(与 bot 命令共用);前端
  `/api/account/me` 显示的是 `account_system` 积分池。别混淆"扣了没退"。
  `refund_rh` 成功现有 INFO 日志,便于对账。

## 验证

```bash
python -m pytest tests/ -q     # 43 passed(含 test_gemini_image / test_seedance_channel /
                               #             test_channel_failover / test_model_catalog / test_model_schema)
ruff check RH_ComfyUI
```
