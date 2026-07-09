审查、修复、文档更新全部完成。最终验证：54 passed（原 49 + 新增 5 个回归用例），ruff 全绿。以下是完整结论。

审查结论：目标基本完成，但有 6 处隐含 bug（已全部修复）

文档声称的架构目标（ABC 全编程式、dispatch 唯一执行路径、校验先于扣费、单层负载均衡、凭证热更新、闭源零依赖）在代码里基本都成立，测试基线也是绿的。但逐行对照后发现以下问题：

已修复的 bug（按严重程度）

1. Gemini Vertex 模式完全打不通（gemini_image/channel.py）：invoke() 用 api_key 做守卫，但 Vertex 模式合法地没有 key（走 ADC/服务账号）。check_available() 放行、invoke() 秒拒。已改为与可用性判定同源的 is_configured()。
2. Dry-Run / 任务取消会吞积分（core/dispatch/dispatcher.py）：DryRunInterrupt 继承 BaseException，asyncio.CancelledError 同理——都绕过 dispatcher 的 except Exception，导致预扣的积分不退款、统计不落库。已改为按 BaseException 兜底：落统计（CancelledError 记 status=cancelled，与 DB 文档声明的三态对齐）→ 退款 → 原样抛出。
3. 非重试错误误计熔断（core/base/generation.py）：record_failure 在判断 retryable 之前调用，用户反复提交坏参数会把健康通道推入 120 秒冷却期，与文档 §4.4 的语义直接矛盾。已改为仅 retryable=True 记失败。
4. 失败路径可能跳过退款（core/telemetry/recorder.py）：record_dispatch 声称"永不抛出"但本体没有兜底，而它在退款之前执行。已整体 try/except 加固。
5. Seedance 生成成功但下载失败时抛裸 httpx 错误：traceback 直接糊到用户脸上。已翻译成 ChannelError(retryable=False)（切通道会重新生成、重复烧钱，所以不重试）。
6. 负载均衡策略/阈值冻结在首次调用（core/routing/balancer.py）：违反"配置改完即生效"的自家红线。已改为 config_resolver 每次决策实时读 SERVICE_CONFIG。

已完成的优化

- HTTP 清单补录纯编程式模型：文档承诺"注册即三入口可见"，但 /RH_ComfyUI/models 只列带 NodeDef 的模型，闭源插件按路径 C 注册的纯 ABC 模型在画布上不可见。/models/summary 的可用数也仍按 Adapter 判定（Seedance/Gemini 永远算不可用）。两处都已修正。
- 去重：rh_generate 里 25 行透明图合白底实现与 utils/image_process.py 完全重复，已改为共享导入。
- Seedance_Dry_Run 配置描述与实际行为对齐（原来写的异常类型是错的）；api.py 的 __all__ 补上 get_model_input_schema；给 openai_image/gemini_image 两个目录补 README（红线 9 的要求）。

每个 bug 都配了回归测试（新增 5 个用例，分布在 test_dispatcher_billing / test_channel_failover / test_gemini_image / test_model_catalog）。

文档修正（11 个文件 + 新增 1 章）

- 新增 13 章《OpenAI 兼容供应商池》：最近一次提交（"对齐后端配置池"）引入的 OpenAI_Image_Providers 配置池、rh 刷新供应商 命令、凭证即时生效 vs 绑定需重挂的语义——此前文档完全没写。
- 纠错：06 章 canvas 入口的 entry_point 实为 "http" 不是 "canvas"（文档自相矛盾，以代码为准）；03 章 requirements 字段只进 HTTP 清单展示、不驱动可用性；docs/ABC_ARCHITECTURE.md 根本不存在，两处引用已清理；08 章红线 5 按现实收窄为"不新增上层依赖"并列明四个既有例外。
- 补全：08 章测试表从 4 个文件补到 10 个；10 章补 刷新供应商 命令和 rh_agent 包（AgentNode 注册，此前无文档）；09 章配置表补 Gemini_Image_Use_Vertex、OpenAI_Image_Providers；测试数 43 → 54 并去掉易过时的硬编码。

一个环境提醒

跑测试必须用 F:/gsuid_core/.venv/Scripts/python.exe——系统 Python 3.13 装的 starlette 1.0.0 与 fastapi 0.112 不兼容，收集阶段就会报 Router.__init__() got an unexpected keyword argument 'on_startup'。这一点已写进 SKILL.md 的验证命令说明和我的记忆。

改动未提交，都在工作区（10 个源码文件、4 个测试文件、11 个文档文件、3 个新文件），需要的话我可以帮你整理成提交。

✻ Brewed for 25m 33s

※ recap: 按文档审查 RH_ComfyUI 全库已完成：修复 6 个 bug（Gemini Vertex、Dry-Run 吞积分等）并更新文档，测试 54 全绿。改动尚未提交，下一步是确认后整理提交。 (disable recaps in /config)

❯ 现在整个框架是否符合预期目标：多个模型，模型有多个渠道/供应商，统一的网页端input schema输出，并且自动负载均衡+openai配置池+不同渠道的供应商请求方式，并且就算供应商是gemini-3.1-flash-image, 在内部也能正确的映射为nano banana 2，并且插件框架支持其他插件来拓展供应商/渠道，同样接入均衡负载，实现一个完美的AIGC生成基座框架。现在你觉得还需要补充什么细节，下一步该做什么？

结论：预期目标已经达成，架构是成立的

逐条对照你说的目标（以实际代码为准）：

┌──────────────────────────┬──────┬──────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│           目标           │ 状态 │                                                   证据                                                   │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 多个模型                 │ ✅   │ model_registry 四模态 16+ 模型，defs.py 全编程式                                                         │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 一个模型多渠道/供应商    │ ✅   │ channel_bindings() 返回多绑定；seedance2 = ark + runninghub + 外部注入；banana2 = gemini + 配置池        │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 统一网页端 input_schema  │ ✅   │ /RH_ComfyUI/models 序列化 PortSpec，含 accepts_images/channels/execution_mode；纯 ABC 模型昨天起也进清单 │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 自动负载均衡 + 熔断      │ ✅   │ 通用 LoadBalancer 三策略、(scope, member) 熔断、配置热更新                                               │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ OpenAI 配置池            │ ✅   │ OpenAI_Image_Providers 网页配置 → register_binding 挂到现有模型                                          │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 各渠道各自的请求方式     │ ✅   │ Adapter / SDK / REST 各自封装在通道内，错误统一翻译成 ChannelError                                       │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ vendor model 映射        │ ✅   │ ChannelBinding.vendor_model：对外永远是 banana2，内部各家用各家的 ID，统计落 backend_model               │
├──────────────────────────┼──────┼──────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 外部插件拓展并入负载均衡 │ ✅   │ channel_registry.register_binding() + entry points + RH_ComfyUI.core 稳定接口                            │
└──────────────────────────┴──────┴──────────────────────────────────────────────────────────────────────────────────────────────────────────┘

骨架没有缺失。剩下的是"从成立到完美"的细节，我按优先级给你排一下。

还缺什么（按值得做的顺序）

P0 — 通道级能力过滤（当前最大的架构空洞）

input_schema 是模型级的，但能力实际是通道级的。例子：banana_pro 声明支持编辑（有 images 端口），你经配置池给它挂一家只支持文生图的供应商——用户带图请求会被负载均衡分给这家，上游报错 → retryable=True → 白白 failover 一轮。另外 openai_image/api.py 把参考图塞进 /images/generations 的 image 字段，这不是标准 OpenAI 协议（官方编辑走 /images/edits multipart），千帆吃这套、别家未必。

建议：给 ProviderChannel 加一个 supports(request) -> bool（默认 True），run() 排序后过滤掉不支持当前输入形态的通道；配置池每行加一个"支持参考图"开关映射到它。这是小改动，堵住的是真实的烧钱路径。

P0 — HTTP 契约快照测试

"契约只增不改"目前靠人肉遵守。加一个 golden test：断言 ModelEntry.to_dict() 的既有字段名和类型永远存在。十几行代码，把最高红线之一变成 CI 强制。

P1 — 动态计费钩子

point_cost 是模型级静态值：Seedance 480p/5s 和 1080p/15s 扣一样的分，flex/draft 档的折扣也体现不了。建议给 ABC 加 estimate_cost(request) -> int（默认返回 point_cost），dispatcher 改用它 reserve。这是业务上迟早要的，早做接口早稳定。

P1 — 通道级对账视图

数据都在（RHComfyuiTaskRecord 有 backend_provider/backend_key_prefix/elapsed_ms/status），但没有聚合出口。加一个 rh 供应商统计 命令：按 provider 聚合成功率、平均耗时、消耗分数。有多供应商就一定有"这家到底稳不稳/值不值"的运维问题，现在只能看内存里的熔断快照（重启即清零）。

P1 — 配置池泛化

现在只覆盖图片模态（_IMAGE_MODEL_REAL_NAMES），每家 weight 固定为 1。顺手可做：每行加权重字段（weighted 策略才有意义）；把池的键名设计成可扩展到 speech/video 模态的形态，哪怕先不实现。

P2 — 弹性细节

- 同通道有限重试：瞬时 429/503 现在直接换通道，先在原通道退避重试 1 次更省钱；
- dispatch 级超时预算：排队发生在扣费后，一个卡死的上游会占全局并发闸最长 1800s；
- Max_Concurrency 现在冻结在首次使用（这是我这轮唯一没改的"配置不热更新"点，改动涉及信号量重建，值得单独做）；
- api.py 里 ordered_content 的视频预处理可以下沉到 VideoGenerationBase.normalize()，消掉入口层最后一块重复逻辑。

下一步建议

按"先堵漏、再增值"的顺序：通道级 supports() 过滤 + 契约快照测试这两个一起做（半天量级，都有现成测试基建），然后做动态计费钩子（接口先行），再做供应商统计命令。P2 放到有真实痛点再动。

要我现在就把 P0 的两项做掉吗？

✻ Baked for 1m 25s

❯ 对，其实千帆也不支持这套，这里其实是我写错了，确实是/images/edits协议，你需要修改，另外我觉得你担心的问题并不存在：同个模型肯定是同种能力，无论在哪里的供应商，所以我们只需要做好同模型的后端可以支持不同供应商的请求方式，并对齐接口就行，主要核心是ABC的基准类。然后另外的P0P1P2都需要修复/优化，并补充相关文档

明白：编辑走 /images/edits 协议（我来改），通道能力过滤不做（同模型同能力，对齐靠 ABC 基准类），其余 P0/P1/P2 全部落地并补文档。先读需要动的几个文件。