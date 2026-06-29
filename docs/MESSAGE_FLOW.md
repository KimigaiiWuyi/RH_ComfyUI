# RH_ComfyUI 消息流转详解

> **对应代码版本**：v2.x（声明式 Pipeline + Adapter 后端抽象 + Seedance 多供应商 + rh_models Web API）
>
> 本文档基于现行代码重写。旧版描述的「PRIORITY 硬编码」「image2image / text2image 细分任务」「image_id_list / image_id 两种图片入口」等机制已不再适用；具体见各章节末尾的 ⚠ 提示。

## 目录

- [一、消息入口](#一消息入口)
- [二、命令触发流程](#二命令触发流程)
- [三、AI Agent 调用流程](#三ai-agent-调用流程)
- [四、各命令的详细流转](#四各命令的详细流转)
- [五、错误处理与降级](#五错误处理与降级)
- [六、并发控制机制](#六并发控制机制)

---

## 一、消息入口

RH_ComfyUI 作为 GsCore 插件，所有消息通过 GsCore 框架的触发器机制接收。

### 插件注册

```python
# RH_ComfyUI/__init__.py
Plugins(
    name="RH_ComfyUI",
    force_prefix=["rh", "cf", "RH"],  # 用户必须以这些前缀开头
    allow_empty_prefix=False,
)
```

### 触发器匹配规则

用户消息到达时，GsCore 框架按以下顺序匹配：

1. 检查消息是否以 `force_prefix`（`rh`/`cf`/`RH`）开头
2. 去掉前缀后，匹配 `on_command` 的关键词
3. 匹配成功后，剩余文本存入 `ev.text`

**示例：**

| 用户消息 | 前缀 | 命令 | ev.text |
|---------|------|------|---------|
| `rh 生图 qwen 一只猫` | `rh` | `生图` | `qwen 一只猫` |
| `cf 改图 把背景换海边` + 图片 | `cf` | `改图` | `把背景换海边` |
| `RH 生视频 一只猫追蝴蝶` | `RH` | `生视频` | `一只猫追蝴蝶` |
| `rh 模型列表` | `rh` | `模型列表` | `` |
| `rh 查询积分` | `rh` | `查询积分` | `` |

### 多模态附件

`rh_generate` 通过 `ev.image_id` / `ev.image_id_list` / `ev.audio_id` / `ev.audio_id_list` 接收多模态附件，并通过 `RM.get(id)` 取回原始字节：

| 字段 | 来源 | 用途 |
|------|------|------|
| `ev.image_id` / `ev.image_id_list` | 用户发送的图片 | 生图/改图（`request.images`）、生视频（首帧/首尾帧/参考） |
| `ev.audio_id` / `ev.audio_id_list` | 用户发送的音频 | 语音克隆（`request.reference_audio`）、多模态视频（`request.audio_refs`） |
| `ev.file` / `ev.file_name` / `ev.file_type` | 用户发送的音频文件 | 语音克隆（base64 / url 两种格式） |

> ⚠ **变更提示**：旧文档描述「`ev.image_id` vs `RM.get(image_id)` 两套图片入口」已统一；现在所有入口都通过 `RM.get(image_id)` 取出原始字节，再视情况 `_flatten_transparent_image_to_white()` 合成白底。

---

## 二、命令触发流程

### 统一流程（所有生成命令共享）

```
用户消息到达
    │
    ▼
GsCore 框架匹配 force_prefix + on_command
    │
    ▼
调用命令处理函数 (generate_image / edit_image / ...)
    │
    ├─ 1. 参数解析
    │     ├─ parse_model_from_prompt(ev.text, task_type)
    │     │   → (model_token, actual_prompt)
    │     │   ⚠ 只返回 token,真正的 NodeDef 选择交给 Router
    │     └─ (语音场景) parse_mood_from_prompt(actual_prompt)
    │         → (mood, remaining_text)
    │
    ├─ 2. 收集多模态附件
    │     ├─ image_id / image_id_list → RM.get() → bytes → request.images
    │     ├─ audio_id / audio_id_list → RM.get() → MediaRef → request.audio_refs
    │     └─ ev.file (URL / base64) → request.reference_audio
    │
    ├─ 3. 构建 GenerationRequest
    │     GenerationRequest(
    │       task_type = 自动推断(IMAGE/VIDEO/MUSIC/SPEECH),
    │       prompt = actual_prompt,
    │       model = model_token,
    │       images = [图片bytes] (如有),
    │       audio_refs = [MediaRef] (如有),
    │       reference_audio = bytes (语音克隆时),
    │       mood = ... (语音时)
    │     )
    │
    └─ 4. 调用 _do_generate(request, ev, bot)
          │
          ├─ 4a. 智能路由 route(request)
          │       → NodeDef(name, backend, point_cost, ...)
          │
          ├─ 4b. 积分检查 check_point(ev, node.point_cost)
          │       → (success: bool, msg: str)
          │       ⚠ 使用 node.point_cost (YAML 声明),非模块常量
          │
          ├─ 4c. 执行生成 execute_generation(request, node, on_progress=...)
          │       → GenerationResult
          │           ├─ adapter.execute(request, node, on_progress)
          │           ├─ _save_output() 落盘到 OUTPUT_PATH
          │           └─ GenerationResult.from_node_output()
          │
          └─ 4d. 失败自动退还积分
                  → RHBind.add_point(ev.user_id, ev.bot_id, node.point_cost)
```

### `_do_generate()` 详细流程

```python
async def _do_generate(request, ev, bot, *, on_progress=None) -> Optional[GenerationResult]:
    # ── Step 1: 智能路由 ──
    try:
        node = await route(request)
    except ModelUnavailableError as e:
        ai_return(f"错误：{e.reason}")
        await bot.send(f"❌ {e.reason}")
        return None

    # ── Step 2: 积分检查 ──
    success, msg = await check_point(ev, node.point_cost)
    if not success:
        ai_return(f"错误：积分不足，需要{node.point_cost}积分")
        await bot.send(msg)
        return None
    await bot.send(f"{msg}\n🎯 使用模型: {node.display_name}")

    # ── Step 3: 进度回调(包装后透传给 executor) ──
    async def _wrapped_progress(event):
        await bot.send(f"⏳ {event.message}")
        if on_progress is not None:
            try:
                await on_progress(event)
            except Exception:
                pass

    # ── Step 4: 执行生成 ──
    try:
        result = await execute_generation(request, node, on_progress=_wrapped_progress)
        ai_return(f"生成完成，使用模型: {node.display_name}")
        return result
    except Exception as e:
        # ── 失败自动退还积分 ──
        logger.exception(f"[RHComfyUI] 生成失败: {e}")
        await RHBind.add_point(ev.user_id, ev.bot_id, node.point_cost)
        refund_msg = f"已退回{node.point_cost}积分。"
        ai_return(f"错误：生成失败 - {str(e)}。{refund_msg}…")
        await bot.send(f"❌ 生成失败: {e}\n↩️ {refund_msg}")
        return None
```

> ⚠ **变更提示**：旧版 `_do_generate()` 仅"返回错误信息"，新版在 catch 块中**自动调用 `RHBind.add_point()` 退还积分**——这是用户感知最强的一处改进。

---

## 三、AI Agent 调用流程

### 通过 `to_ai=` 注册的工具

`rh_generate` 中每个生成命令都通过 `to_ai=` 参数注册为 AI 工具。当 AI Agent 决定调用时：

```
AI Agent 决定: 调用 generate_image(text="一只可爱的猫咪", image_id=None)
    │
    ▼
GsCore 创建 MockBot + MockEvent
    │  MockEvent.text = "一只可爱的猫咪"
    │  MockEvent.image_id = None (或从参数获取)
    │
    ▼
调用 generate_image(bot=MockBot, ev=MockEvent)
    │
    ├─ MockBot.send("文字") → 文字被收集,作为工具返回值
    ├─ MockBot.send(bytes/图片) → 通过 RM.register() 注册,返回资源ID
    ├─ MockBot.send(MessageSegment.record(...)) → 注册音频资源
    │
    └─ ai_return("文本摘要") → 文本被收集,作为工具返回值
    │
    ▼
AI Agent 收到工具返回值(文字 + 资源ID)
    │
    ▼
AI Agent 决定是否调用 send_message_by_ai(image_id="img_xxx")
    │
    ▼
用户收到生成结果
```

**AI Agent 可用的生成工具（来自 `rh_generate` 的 `to_ai=` 注册）：**

| 工具 | 用途 |
|------|------|
| `generate_image` | 文生图 / 图片编辑（按是否带图自动切换） |
| `edit_image` | 图片编辑（必填 image_id_list） |
| `generate_video` | 文生视频 / 图生视频 / 首尾帧 / 多模态（按输入自动适配） |
| `generate_music` | 音乐生成 |
| `generate_speech` | 语音合成（含情绪 `[xxx]` 标签 + 参考音频克隆） |
| `list_models` | 查看所有可用模型 |
| `model_detail` | 查看单个模型详情 |

> ⚠ **变更提示**：旧文档描述 `generate_image` 同时支持文生图 / 图生图 / 编辑三类（按 `request.images` 路由），新版进一步**取消了独立的 image2image 潜空间重绘**，统一为「0 张图 = 文生图 / 1+ 张图 = 图片编辑」二态（由 Router 输入档案过滤决定）。

### 通过 `@ai_tools` 注册的工具

积分管理函数通过 `@ai_tools` 注册（`rh_admin/commands.py`）：

```
AI Agent 决定: 调用 query_user_points(target_user_id="12345")
    │
    ▼
GsCore 创建 ToolContext (包含 bot, ev)
    │
    ▼
调用 query_user_points(target_user_id="12345", ev=MockEvent)
    │  check_func=check_pm 触发:管理员才能调用 add/deduct
    │
    └─ 返回 "👤 用户 [12345] 的当前积分: 20"
    │
    ▼
AI Agent 收到返回值,用于回复用户
```

### 通过 `ai_entity` 注册的 Pipeline 知识库

`rh_generate/_knowledge.py` 把每个 Pipeline 注册为 `KnowledgePoint`，让 LLM 在 system prompt 中看到所有可用模型：

```
AI Agent 上线
    │
    ▼
register_pipeline_knowledge() 把每个 Pipeline 写入 ai_entity
    │  KnowledgePoint(
    │    id="rh_comfyui_pipeline_<name>",
    │    title="<display_name> - <task_type>",
    │    content="基本信息 + 描述 + 详细说明 + 使用方式",
    │    tags=["RH_ComfyUI", "AIGC", <task_type>, <name>, <display_name>]
    │  )
    │
    ▼
LLM 在选择模型时,直接检索这些 KnowledgePoint
```

> ⚠ **变更提示**：旧文档描述「`register_pipeline_knowledge()` 把 Pipeline 注入 `to_ai=` 字段」，新版改用 `ai_entity(KnowledgePoint(...))` 单独存储，由 RAG 检索而非命令字符串。

### `rh_models` 的 AI 工具

`rh_models/utils.py` 提供 `ai_list_models(task_type="")`：

```
AI Agent 决定: 查看可用模型
    │
    ▼
ai_list_models(task_type="image")
    │
    ▼
build_model_catalog(include_unavailable=False, task_type="image", as_text=True)
    │
    └─ 返回 LLM 友好的纯文本(已分任务类型、已剔除不可用项)
        "可用模型(图片生成):
         - Qwen 2512 (id=qwen_2512, backend=comfyui, 2 积分)
         - GPT-Image2 (id=gpt-image-2, backend=gpt-image-2, 2 积分)
         ..."
```

---

## 四、各命令的详细流转

### 4.1 生图（文生图 / 编辑自适应）

```
用户: "rh 生图 qwen 一只可爱的猫咪"
    │
    ▼
generate_image()
    │
    ├─ ev.text = "qwen 一只可爱的猫咪"
    ├─ ev.image_id = None (无附件) → request.images 为空
    ├─ parse_model_from_prompt() → model_token="qwen", actual_prompt="一只可爱的猫咪"
    │
    ├─ GenerationRequest(
    │     task_type=IMAGE,         # ⚠ 不再细分 text2image / image2image
    │     prompt="一只可爱的猫咪",
    │     model="qwen",
    │     images=[]                # 0 张图
    │  )
    │
    └─ _do_generate()
         ├─ route() → NodeDef(name="qwen_2512", backend="comfyui", point_cost=2)
         │           ⚠ Router 输入档案过滤:images=[] 排除要求必填 images 的节点(如 qwen_2511)
         ├─ check_point(ev, 2) → ✅
         └─ execute_generation()
              └─ ComfyUIAdapter.execute()
                   ├─ load_workflow("qwen_2512.json")
                   ├─ _apply_declarative_mappings()
                   │   ├─ prompt → workflow["108"]["inputs"]["text"]
                   │   ├─ width(720) → workflow["107"]["inputs"]["width"]
                   │   └─ height(1280) → workflow["107"]["inputs"]["height"]
                   └─ api.generate_image_by_prompt(workflow)
                        ├─ queue_prompt() → POST /prompt
                        ├─ track_progress() → WebSocket 监听
                        └─ get_images() → GET /view 下载图片
    │
    ▼
bot.send("✅ 图片生成完成！")
bot.send(await convert_img(result.data))
```

**带图自动转入编辑：**

```
用户: "rh 生图 把背景换成海边" + 附带图片 (ev.image_id="img_xxx")
    │
    ▼
generate_image()
    │
    ├─ ev.image_id = "img_xxx"
    ├─ RM.get("img_xxx") → bytes → _flatten_transparent_image_to_white() → request.images=[bytes]
    │
    ├─ GenerationRequest(task_type=IMAGE, prompt="把背景换成海边", images=[bytes])
    │
    └─ _do_generate()
         ├─ route() → NodeDef(name="qwen_2511", backend="comfyui", point_cost=4)
         │           ⚠ Router 输入档案过滤:images=[1 byte] 排除 0 张图节点,匹配要求 images 必填的节点
         └─ execute_generation()
              └─ ComfyUIAdapter.execute()
                   ├─ load_workflow("qwen_edit_2511.json")
                   └─ qwen_edit_mapper(request, workflow, api)
                        ├─ workflow["103"]["inputs"]["text"] = "把背景换成海边"
                        ├─ workflow["41"]["inputs"]["image"] = await api.upload_image(bytes)
                        └─ workflow["73"]["inputs"]["image"] = ... (若多张)
```

### 4.2 改图（图片编辑显式入口）

```
用户: "rh 改图 把背景换成海边" + 附带图片
    │
    ▼
edit_image()
    │
    ├─ ev.text = "把背景换成海边"
    ├─ ev.image_id_list = ["img_xxx"] (或 ev.image_id="img_xxx")
    │    ⚠ 兼容 AI 通过 image_id 参数传入资源 ID
    ├─ image_id_list 为空 → 直接报错 "编辑图片需要附带至少一张图片！"
    │
    ├─ RM.get() 取所有图片 → [_flatten_transparent_image_to_white(b) for b in bytes]
    │
    ├─ GenerationRequest(task_type=IMAGE, prompt="把背景换成海边", images=[b1, b2, b3])
    │
    └─ _do_generate()
         ├─ route() → NodeDef(name="qwen_2511", ...)
         ├─ check_point(ev, 4) → ✅
         └─ execute_generation()
              └─ ComfyUIAdapter.execute()
                   └─ qwen_edit_mapper(最多 3 张图,详见 mappers/image_edit.py)
    │
    ▼
bot.send("✅ 图片编辑完成！")
bot.send(await convert_img(result.data))
```

> ⚠ **变更提示**：`edit_image()` 与 `generate_image()` 现在职责明确分离：`edit_image` 是**显式图片编辑入口**（要求必带图），`generate_image` 是**自适应入口**（按是否带图自动选文生图或编辑）。

### 4.3 生视频（按输入自动适配形态）

```
用户: "rh 生视频 一只猫在草地上追蝴蝶"
    │
    ▼
generate_video()
    │
    ├─ ev.text = "一只猫在草地上追蝴蝶"
    ├─ ev.image_id = None → request.images = []
    ├─ ev.audio_id = None → request.audio_refs = []
    │
    ├─ GenerationRequest(task_type=VIDEO, prompt="...", model=...)
    │
    └─ _do_generate()
         ├─ route() → NodeDef(name="wan2.2_videogen" 或 "seedance2", ...)
         │           ⚠ 取决于可用性 + 优先级(Seedance 默认 priority=90 高于 Wan=70)
         ├─ check_point(ev, 15) → ✅
         └─ execute_generation()
              ├─ 若 Wan 节点: wan_videogen_mapper
              │    ├─ 0 张图 → 走文生视频节点 ID (37/33/44/34)
              │    ├─ set_workflow_override("wan2.2_t2v.json")  # 默认即 t2v
              │    ├─ workflow[37]["inputs"]["text"] = "一只猫在草地上追蝴蝶"
              │    ├─ workflow[44]["inputs"]["value"] = 720
              │    ├─ workflow[34]["inputs"]["value"] = 1280
              │    └─ workflow[33]["inputs"]["value"] = 5
              │
              └─ 若 Seedance 节点: classify → provider.run
                   ├─ classify_video_spec(): 0 张图 → shape=TEXT2VIDEO
                   ├─ resolve_provider_candidates(): 按 Load_Balance 选 ark/gateway/runninghub
                   ├─ provider.run(): POST /contents/generations/tasks
                   ├─ poll_until_done(): GET /contents/generations/tasks/{id} 轮询
                   └─ 返回 video_url → _download()
    │
    ▼
bot.send("✅ 视频生成完成！")
bot.send(MessageSegment.video(result.data))

# 尾帧图(若 Seedance 返回)
last_frame = (result.outputs or {}).get("last_frame")
if last_frame:
    bot.send("🎞️ 视频尾帧图：")
    bot.send(await convert_img(last_frame))
```

**带图场景的自动形态切换（按 `request.images` 数量）：**

| 图片数 | Router 决策 | ComfyUI Wan 行为 | Seedance 行为 |
|--------|------------|------------------|---------------|
| 0 | T2V | 走文生视频节点，t2v workflow | shape=TEXT2VIDEO |
| 1 | I2V | 上传首帧 → i2v workflow，节点 67 | shape=IMAGE2VIDEO |
| 2+ | 首尾帧 / 多模态 | 仅取首帧（节点 67），其余交给 Seedance | shape=FIRST_LAST_FRAME 或 MULTIMODAL |
| 图 + 音/视频 | 多模态 | — | shape=MULTIMODAL（content[] 有序排列） |

> ⚠ **变更提示**：旧文档描述「`wan2.2_text2video.yaml` / `wan2.2_img2video.yaml` 两个独立节点」，新版合并为 **一个 `wan2.2_videogen` 节点 + `wan_videogen_mapper`**，按图片数自动选 workflow 并通过 `set_workflow_override()` 通知 Adapter 重新加载工作流。

### 4.4 生音乐

```
用户: "rh 生音乐 轻松愉快的背景音乐"
    │
    ▼
generate_music()
    │
    ├─ task_type = MUSIC
    ├─ parse_model_from_prompt() → model_token="ace_step1.5", actual_prompt="轻松愉快的背景音乐"
    │
    └─ _do_generate()
         ├─ route() → NodeDef(name="ace_step1.5", backend="comfyui", point_cost=2)
         ├─ check_point(ev, 2) → ✅
         └─ execute_generation()
              └─ ComfyUIAdapter.execute()
                   ├─ load_workflow("ace_step1.5.json")
                   └─ ace_step_mapper(request, workflow, api)
                        ├─ workflow["131"]["inputs"]["text"] = "轻松愉快的背景音乐"
                        └─ workflow["130"]["inputs"]["text"] = "" (歌词留空)
    │
    ▼
bot.send("✅ 音乐生成完成！")
bot.send(MessageSegment.record(result.data))
```

### 4.5 生语音

```
用户: "rh 生语音 欢迎使用RH_ComfyUI" 或 "rh 生语音 [happy] 欢迎使用"
    │
    ▼
generate_speech()
    │
    ├─ task_type = SPEECH
    ├─ parse_model_from_prompt() → model_token="indextts"|"minimax"|"mimo"
    ├─ parse_mood_from_prompt(actual_prompt) → (mood, remaining_text)
    │    ⚠ 解析 [情绪] / [情绪:xxx] / [mood:xxx] 标签
    │
    ├─ 附加参考音频(语音克隆音色):
    │    ├─ ev.audio_id → RM.get() → request.reference_audio = bytes
    │    └─ ev.file (URL / base64) → request.reference_audio = bytes
    │
    ├─ GenerationRequest(task_type=SPEECH, prompt=remaining_text, model=token, mood=...)
    │
    └─ _do_generate()
         ├─ route() → NodeDef("IndexTTS2"/"minimax_t2a_speech"/"mimo_tts", ...)
         ├─ check_point(ev, 2/3) → ✅
         └─ execute_generation()
              ├─ IndexTTS2: index_tts2_mapper
              │    ├─ workflow["14"]["inputs"]["value"] = "欢迎使用RH_ComfyUI"
              │    ├─ request.reference_audio → workflow["13"]["inputs"]["audio"]
              │    └─ request.mood → workflow["40"]["inputs"]["value"]
              ├─ MiniMax: minimax_t2a_speech_mapper
              │    ├─ 必要时上传音频 → MiniMax voice_clone → 缓存 voice_id
              │    └─ api.generate_speech(text, voice_id, speed, emotion, ...)
              └─ MiMo: mimo_tts_mapper
                   ├─ 有 reference_audio → 自动选 mimo-v2.5-tts-voiceclone
                   ├─ 无 reference_audio → mimo-v2.5-tts
                   └─ api.generate_speech(text, mood, reference_audio, ...)
    │
    ▼
bot.send("✅ 语音生成完成！")
bot.send(MessageSegment.record(result.data))
```

---

## 五、错误处理与降级

### 路由阶段错误

```
route(request)
    │
    ├─ 用户指定 model 不存在 → 输入感知的部分名匹配:
    │     ├─ 同 TaskType 桶内按名字 token 过滤(_node_supports_request 输入档案)
    │     ├─ 命中 → 检查可用性 → ✅ 返回
    │     └─ 未命中 → 警告日志 → 回退自动选择
    │
    ├─ 用户指定 model 不可用 → 警告日志 → 回退自动选择
    │
    ├─ 没有任何候选(同模态+输入档案) → ModelUnavailableError("没有与当前输入匹配的节点")
    │
    ├─ 所有候选都不可用 → ModelUnavailableError("所有 N 个候选后端均不可用:\n- 后端1: ...\n- 后端2: ...")
    │
    └─ AI 推荐失败 → 警告日志 → 回退优先级兜底
```

> ⚠ **变更提示**：旧版路由只做精确/前缀/包含匹配，新版在用户指定失败时还会**用 `_node_supports_request()` 做输入档案过滤**——避免「`qwen` 在带图场景被错配到纯文生图的 `qwen_2512`」这类 bug。

### 积分阶段错误

```
check_point(ev, point)
    │
    ├─ 积分充足 → ✅ 自动扣除
    │    return True, "💪 积分充足!已扣除{point}积分!\n📋 当前积分: {now_point}\n✅ 正在生成..."
    │
    └─ 积分不足 → ❌
         return False, "❌ 积分不足!需要{point}积分!\n📋 当前积分: {now_point}"
         → ai_return("错误：积分不足,需要{point}积分")
         → bot.send(msg)
         → return None(终止流程)
```

> ⚠ **变更提示**：`_do_generate` 使用 **`node.point_cost`**（YAML 声明），而非 `utils/points.py` 顶部的模块常量 `Draw_Point` / `Speech_Point` / `Video_Point` 等——后者仅为向后兼容保留，实际业务以 NodeDef 为准。

### 执行阶段错误

```
execute_generation(request, node, on_progress=…)
    │
    ├─ Adapter 未注册 → RuntimeError(f"Adapter {node.backend} 未注册")
    │
    ├─ Workflow 文件不存在 → RuntimeError(f"ComfyUI 节点 {node.name} 工作流文件不存在: ...")
    │
    ├─ Adapter.execute() 抛出任意 Exception
    │
    └─ _do_generate() 捕获后:
         ├─ logger.exception(f"[RHComfyUI] 生成失败: {e}")
         ├─ await RHBind.add_point(ev.user_id, ev.bot_id, node.point_cost)
         │   ⚠ 自动退还积分(本次关键改进)
         ├─ ai_return(f"错误：生成失败 - {str(e)}。已退回{point_cost}积分。…")
         └─ bot.send(f"❌ 生成失败: {e}\n↩️ 已退回{point_cost}积分。")
         → return None(终止流程)
```

### Seedance 多供应商降级

```
SeedanceAdapter.execute(request, node)
    │
    ├─ _resolve_candidates(node)
    │    ├─ 节点级固定 provider → [provider] (单元素)
    │    └─ 否则按 Seedance_Enable_* 收集启用供应商,按 Load_Balance 排序
    │         ├─ round_robin: 轮转
    │         ├─ weighted: 加权随机(ark=3, gateway=2, runninghub=1)
    │         └─ least_failures: 失败最少优先
    │
    ├─ 逐个 provider 尝试:
    │    ├─ 无 API key → skip + 警告
    │    ├─ provider.run() 成功 → 记录 success + 返回 NodeOutput
    │    └─ provider.run() 失败(SeedanceProviderError / 网络错误):
    │         ├─ UnsupportedProviderShapeError → 不可重试,直接抛出(不降级)
    │         ├─ DRY_RUN_BLOCKED → Dry-Run 拦截,直接抛出(不降级)
    │         └─ 其他 → record_provider_failure(name),连续失败达阈值后熔断 120s
    │
    └─ 所有供应商都失败 → RuntimeError("Seedance 所有供应商均不可用: …")
```

---

## 六、并发控制机制

### 全局 Semaphore

```python
# utils/core/executor.py
_generation_semaphore: asyncio.Semaphore | None = None

def _get_semaphore() -> asyncio.Semaphore:
    global _generation_semaphore
    if _generation_semaphore is None:
        from ...rh_config.comfyui_config import PLUGIN_CONFIG
        concurrency = PLUGIN_CONFIG.get_config("Max_Concurrency").data
        if not isinstance(concurrency, int) or concurrency < 1:
            concurrency = 1
        _generation_semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[Executor] 全局并发限制初始化: {concurrency}")
    return _generation_semaphore
```

- 所有后端（ComfyUI / GPT-Image2 / RH App / MiniMax / MiMo / Seedance）**共享同一个 Semaphore**
- 并发数由 `PLUGIN_CONFIG.Max_Concurrency` 控制（默认 1，选项 1/2/3/5/10）
- 超出并发限制的请求会在 `async with sem:` 处等待

### 执行流程中的限流点

```
execute_generation()
    │
    ├─ 获取 Semaphore(可能等待)
    │
    ├─ async with sem:  ← 限流点
    │     ├─ adapter.execute(request, node, on_progress=…)
    │     ├─ _save_output() 落盘
    │     └─ GenerationResult.from_node_output()
    │
    └─ 释放 Semaphore(自动)
```

### 输出落盘

生成完成后，`executor._save_output()` 自动把产物写到 `OUTPUT_PATH/<task_type>/`：

```
OUTPUT_PATH/
├── image/
│   ├── 1719628800000.png       # 主产物(单产物场景)
│   └── 1719628800000_image.png # 附加产物(如 Seedance 视频生成的尾帧图)
├── video/
│   ├── 1719628801000.mp4       # 主产物
│   └── 1719628801000_last_frame.png
├── music/
│   └── 1719628802000.mp3
└── speech/
    └── 1719628803000.mp3
```

主产物文件名 = `<时间戳 ms>.<按 OutputType 推断的扩展名>`；附加产物文件名 = `<时间戳 ms>_<语义名>.<嗅探的扩展名>`。

> ⚠ **变更提示**：旧版 executor 不做落盘，产物只通过 `bot.send()` 推送；新版自动写入 `OUTPUT_PATH`，便于调试与历史回溯。`node_output.metadata["saved_path"]` 会回填路径供业务查询。