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
- 熔断只由 `ChannelError(retryable=True)` 触发(retryable=False 不记失败、不切换,
  2026-07-10 起代码与此语义一致);`AllChannelsFailedError` 现继承
  最后一路 `cause.user_message`,保住供应商干净失败文案。
- **`SeedanceProviderChannel` 尊重供应商侧 retryable 标注**(2026-07-10 修正,
  此前硬编码 True):`SeedanceProviderError.retryable` 原样透传成
  `ChannelError.retryable` —— 参数类失败(4xx / 网关显式 retryable=false)不再
  盲目 failover 烧钱。各 raise 点的语义:HTTP 按 `http_status_retryable()`
  统一策略(5xx/429/408/401/403=True,其余 4xx=False)、`TASK_FAILED`/
  `TASK_EXPIRED`/`NO_TASK_ID`/`BAD_RESPONSE`=True、`POLL_NETWORK_ERROR`=False
  (与取件失败同理,任务可能已成功);429/503 额外映射 `transient=True`,
  run() 先在原通道退避重试一次。**新 Provider 抛错必须显式标 retryable**。
- **生成成功但取件失败 ≠ 通道失败**:Seedance 下载 video_url 失败翻译成
  `ChannelError(retryable=False, code=RESULT_DOWNLOAD_FAILED)` —— 切通道会重新
  生成(重复烧钱),只给干净文案让用户重试。
- **Idempotency-Key 只在创建请求携带**(`_create_headers()`,一次 create 一个
  key;查询/删除不带)。共享的 content[] 渲染逻辑已公开为
  `providers/ark.py::ContentArrayMixin`(旧私有名保留别名),外部供应商插件
  继承它复用有序多模态渲染,不要再 import 下划线私有名。
- **Dry-Run 中断会退款**:`DryRunInterrupt` 继承 `BaseException` 绕过 run() 的
  熔断/切换,dispatcher 现按 BaseException 兜底 —— 落统计(failed)+ 退款后原样抛出
  (2026-07-10 修复,此前干跑会吞积分)。

## 2. 消费统计维度

- `backend_provider` = 通道名(`output.metadata["channel"]`,run() 落);
- `backend_key_prefix` = key 前 6 位,来自 `ProviderChannel.audit_key_prefix()`
  (run() 落 metadata,recorder 透传,DB 列已加)。含密钥的通道要记前缀就覆盖
  `audit_key_prefix`。
- 负载均衡策略/阈值键:`Load_Balance_Mode` / `Failure_Threshold`(`PLUGIN_CONFIG`,
  全局一条,改完即生效)。
- **Dry-Run** 只有 `PLUGIN_CONFIG.Dry_Run` 一把开关,开启后 `run()` 拦截全部模型。

## 3. Gemini 生图(banana2 与 banana2.1 并存,价格不同)

- **走官方 `google-genai` SDK 的 `client.aio.models.generate_content`**,不要手拼
  REST/URL/鉴权,也**不要**走 `interactions.create`。Interactions 会把带
  `-preview` 的生图 ID 改写成 `…-agent`,该变体报
  `Image input modality is not enabled`(参考图 400)。正式 ID 没有 `-agent`。
- **不要把 banana2 改成 2.1**。两条目录、两套 schema、两条计费曲线:
  - `banana2`:vendor `gemini-3.1-flash-image-preview`。尺寸 512/1K/2K/4K,
    默认 2K。比例是原来那 9 档,没有思考档。价格 60 美元/1M → 5/7/11/16 积分。
  - `banana2.1`:vendor `gemini-nano-banana-2.1`。尺寸 1K/2K/4K,默认 1K。
    思考档 `thinking_config.thinking_level`(`minimal` / `medium` 默认 / `high`),
    **不改变积分**。比例用官方全景表,产品面另留 8:5(mapper 折成 3:2)。
    价格 30 美元/1M → 4/6/12 积分。4K token 用价格页 **3780**,不是 2 代的 2520。
- 已安装 SDK 的 `GenerateContentConfig` 没有 `response_format`,不要改去发那个字段。
  思考档只在 vendor id 含 `nano-banana-2.1` 时发送。
- **双模互斥(SDK 硬约束)**:`Client(vertexai=True, project=…, api_key=…)` 会抛
  "Project/location and API key are mutually exclusive"。故用**显式开关**
  `Gemini_Image_Use_Vertex`:
  - 关(默认)= AI Studio → `Client(api_key=Gemini_Image_apikey)`;
  - 开 = VertexAI → `Client(vertexai=True, project, location)`,鉴权走 ADC 或
    `Gemini_Image_SA_File` 服务账号 JSON,**忽略 api_key**。
  - **别再用"填了 project_id 就走 Vertex"推断** —— 用户填了 key+project 会被迫
    走 Vertex、报 ADC 缺失(踩坑现场)。
- **直连不到 Google 就填 `Gemini_Image_BaseURL`(中转地址)**:经
  `Client(http_options={"base_url": …})` 改道,SDK 仍在其后拼 `/v1beta/…`
  (`api_version` 不变),中转端按标准路径转发即可。**仅 AI Studio 模式生效** ——
  Vertex 有自己的端点体系,套 generativelanguage 的中转前缀会把它打歪。
  留空直连官方端点;跑通与否看日志里的 `endpoint=`。
- **通道内所有守卫用 `is_configured()`,不要按 `api_key` 判**:Vertex 模式合法地
  没有 api_key,`GeminiImageChannel.invoke` 曾按 `api_key` 拒绝导致 Vertex 打不通
  (check_available 放行、invoke 秒拒,2026-07-10 修复)。守卫必须与
  check_available 同源。
- **图片在 `steps` 里,不在 `outputs`**:interactions 响应 `outputs` 常为空,图在
  `steps[*].content[*]` 的 `{type:"image", data:<base64>}`(**inline base64,不是
  uri**)。`usage.output_tokens_by_modality` 里有 image 即已出图。`steps` 是 SDK
  未声明的 extra 字段,取值要 `getattr` + `model_extra` 兜底,内层是原始 dict。
  提取器见 `gemini_image/api.py::_find_image`(先 outputs 再 steps,兼容 data/uri)。
- **`Gemini_Enabled_Models`**:与 MiniMax 同构的 GsListStrConfig。banana1 / banana2 /
  banana2.1 / banana_pro 的 Gemini 通道仅在列表勾选后才 `check_available`。
  已保存的旧列表不会自动勾上 `banana2.1`。banana_pro 的
  gpt-image-2 / 外部通道不受此列表影响。
- **`MiniMax_Enabled_Models` / `DashScope_Enabled_Models` 同理**:只关官方通道,
  不挡 host 模型。`minimax_h3` / `happyhorse1.1` / `wan3.0` 只要有任一外部插件
  通道可用,`/models` 就应 `available=true`。
- **banana2 / banana2.1 都独立于 gpt-image-2**:`backend="gemini-image"`,
  `channel_bindings()` 只挂 `GeminiImageChannel`;请求 **不经过** gpt-image-2
  (OpenAI 兼容)后端。日志里 `[GPT-Image2]` 是**后端名**不是模型名。
- **schema 不用宽高**。catalog 档位进 `params`(见 [§6.6](./06-entry-points.md))。
  banana2 只有 `ratio` + `image_size`。`thinking_level` 只在 banana2.1。
  2.1 宽高比含 1:4、4:1、1:8、8:1、4:5、5:4;产品面 8:5 由 mapper 折成 3:2。

## 4. input_schema 必须与模型能力一致(agent / 调用方据此判参数)

`/api/RH_ComfyUI/models` 的 `input_schema` 是**机器可读的能力契约**:agent(调用方)
按它决定给哪个模型传什么参数,调用方按它渲染表单。规则:

- 有 `images` 端口 ⇒ 支持传参考图;`max_items` ⇒ 上限;
- **纯文生图 / 纯文本模型无 `images` 端口** → 桥接层推出 `supports_edit=False`,
  `ImageGenerationBase.validate` 会拒图(`"纯文生图模型,不接受参考图"`);
- `ModelEntry` 另有显式 `accepts_images` / `max_input_images` 便于直接判定。

**踩坑**:`SeedanceVideoModel.__init__` **强制** `supported_shapes = 全 4 形态`
(文生/图生/首尾帧/多模态),但 `seedance15_pro` / `seedance2_fast` 曾漏声明
`images`/`video_refs`/`audio_refs`/`frame_mode` 端口 → schema 藏了能力,agent/调用方
以为它们只能文生。**改模型 supported_shapes / max_reference_total 时,务必同步
input 端口**。现全部 Seedance 变体端口已对齐。

| 模型 | 参考输入端口 | 能力 |
|---|---|---|
| anima / minimax_image01 / qwen_2512 | 无 | 纯文生图(拒图) |
| banana1 | images(≤3) | 一代 Gemini(gemini-2.5-flash-image),图生/编辑;无 image_size 端口(一代不支持尺寸档,mapper 对 2.5 系自动不发该字段) |
| banana2 | images(≤14) | Nano Banana 2(`gemini-3.1-flash-image-preview`),512/1K/2K/4K 默认 2K |
| banana2.1 | images(≤14) | Nano Banana 2.1(`gemini-nano-banana-2.1`),1K/2K/4K 默认 1K + thinking_level |
| banana_pro / gpt-image-2 / qwen_2511 | images | 图生/编辑 |
| seedance*(全变体)| images/video_refs/audio_refs | 多模态视频 |
| wan2.2_videogen | images(≤2) | 首尾帧 |
| ace_step1.5 | 无(negative_prompt=歌词) | 文生音乐 |
| IndexTTS2 / mimo_tts / minimax_t2a_speech | reference_audio | 音色复刻 |

## 5. 计费 / 退款(HTTP 入口)

- http 入口用 `ExternalPrepaidPolicy`:**引擎只记账不扣费**,扣费/退款由
  HTTP 宿主自行负责。生成失败时宿主自行退还预扣。
- **两套账本勿混**:HTTP 入口走预付记账(引擎统计表仍写 `point_cost`);
  调用方可能另有自己的积分池。别把引擎统计金额当成调用方钱包余额。

## 验证

```bash
python -m pytest tests/ -q     # 全绿(2026-07-10: 73 passed;完整测试清单见 08 章 §8.1)
ruff check RH_ComfyUI
```
