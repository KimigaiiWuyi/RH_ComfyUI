# RH_ComfyUI 消息流转详解

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
| `rh 查询积分` | `rh` | `查询积分` | `` |
| `rh 模型列表` | `rh` | `模型列表` | `` |

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
    │     │   → (model_name, actual_prompt)
    │     └─ 检查图片附件 (ev.image_id / ev.image_id_list)
    │
    ├─ 2. 构建 GenerationRequest
    │     GenerationRequest(
    │       task_type = 自动推断,
    │       prompt = actual_prompt,
    │       model = model_name,
    │       images = [图片bytes] (如有)
    │     )
    │
    └─ 3. 调用 _do_generate(request, ev, bot)
          │
          ├─ 3a. 智能路由 route(request)
          │       → PipelineDef
          │
          ├─ 3b. 积分检查 check_point(ev, pipeline.point_cost)
          │       → (success: bool, msg: str)
          │
          └─ 3c. 执行生成 execute_generation(request, pipeline)
                  → GenerationResult
```

### `_do_generate()` 详细流程

```python
async def _do_generate(request, ev, bot) -> Optional[GenerationResult]:
    # ── Step 1: 智能路由 ──
    try:
        pipeline = await route(request)
    except ModelUnavailableError as e:
        ai_return(f"错误：{e.reason}")
        await bot.send(f"❌ {e.reason}")
        return None

    # ── Step 2: 积分检查 ──
    success, msg = await check_point(ev, pipeline.point_cost)
    if not success:
        ai_return(f"错误：积分不足，需要{pipeline.point_cost}积分")
        await bot.send(msg)
        return None
    await bot.send(f"{msg}\n🎯 使用模型: {pipeline.display_name}")

    # ── Step 3: 执行生成 ──
    try:
        result = await execute_generation(request, pipeline)
        ai_return(f"生成完成，使用模型: {pipeline.display_name}")
        return result
    except Exception as e:
        ai_return(f"错误：生成失败 - {str(e)}")
        await bot.send(f"❌ 生成失败: {e}")
        return None
```

---

## 三、AI Agent 调用流程

### 通过 `to_ai=` 注册的工具

每个生成命令都通过 `to_ai=` 参数注册为 AI 工具。当 AI Agent 决定调用时：

```
AI Agent 决定: 调用 generate_image(text="一只可爱的猫咪")
    │
    ▼
GsCore 创建 MockBot + MockEvent
    │  MockEvent.text = "一只可爱的猫咪"
    │  MockEvent.image_id = None (或从参数获取)
    │
    ▼
调用 generate_image(bot=MockBot, ev=MockEvent)
    │
    ├─ MockBot.send("文字") → 文字被收集，作为工具返回值
    ├─ MockBot.send(bytes/图片) → 通过 RM.register() 注册，返回资源ID
    ├─ MockBot.send(MessageSegment.record(...)) → 注册音频资源
    │
    └─ ai_return("文本摘要") → 文本被收集，作为工具返回值
    │
    ▼
AI Agent 收到工具返回值（文字 + 资源ID）
    │
    ▼
AI Agent 决定是否调用 send_message_by_ai(image_id="img_xxx")
    │
    ▼
用户收到生成结果
```

### 通过 `@ai_tools` 注册的工具

积分管理函数通过 `@ai_tools` 注册：

```
AI Agent 决定: 调用 query_user_points(target_user_id="12345")
    │
    ▼
GsCore 创建 ToolContext (包含 bot, ev)
    │
    ▼
调用 query_user_points(target_user_id="12345", ev=MockEvent)
    │
    └─ 返回 "👤 用户 [12345] 的当前积分: 20"
    │
    ▼
AI Agent 收到返回值，用于回复用户
```

---

## 四、各命令的详细流转

### 4.1 生图（文生图/图生图）

```
用户: "rh 生图 qwen 一只可爱的猫咪"
    │
    ▼
generate_image()
    │
    ├─ ev.text = "qwen 一只可爱的猫咪"
    ├─ ev.image_id = None → task_type = TEXT2IMAGE
    ├─ parse_model_from_prompt() → model="qwen_2512", prompt="一只可爱的猫咪"
    │
    ├─ GenerationRequest(task_type=TEXT2IMAGE, prompt="一只可爱的猫咪", model="qwen_2512")
    │
    └─ _do_generate()
         ├─ route() → PipelineDef(name="qwen_2512", backend="comfyui", point_cost=2)
         ├─ check_point(ev, 2) → ✅
         └─ execute_generation()
              └─ ComfyUIBackend.execute()
                   ├─ load_workflow("qwen_2512.json")
                   ├─ _apply_declarative_mappings()
                   │   ├─ prompt → workflow["108"]["inputs"]["text"]
                   │   ├─ width(720) → workflow["107"]["inputs"]["width"]
                   │   └─ height(1280) → workflow["107"]["inputs"]["height"]
                   └─ api.generate_image_by_prompt(workflow)
                        ├─ queue_prompt() → POST /prompt
                        ├─ track_progress() → WebSocket 监听
                        └─ get_images() → GET /view 下载图片
```

### 4.2 改图（图片编辑）

```
用户: "rh 改图 把背景换成海边" + 附带图片
    │
    ▼
edit_image()
    │
    ├─ ev.text = "把背景换成海边"
    ├─ ev.image_id_list = ["img_xxx"] → task_type = IMAGE_EDIT
    ├─ parse_model_from_prompt() → model=None, prompt="把背景换成海边"
    │
    ├─ RM.get("img_xxx") → 图片bytes
    ├─ _flatten_transparent_image_to_white() → 处理透明图片
    │
    ├─ GenerationRequest(task_type=IMAGE_EDIT, prompt="把背景换成海边", images=[bytes])
    │
    └─ _do_generate()
         ├─ route() → PipelineDef(name="qwen_2511_edit", backend="comfyui")
         ├─ check_point(ev, 4) → ✅
         └─ execute_generation()
              └─ ComfyUIBackend.execute()
                   ├─ load_workflow("qwen_edit_2511.json")
                   └─ qwen_edit_mapper(request, workflow, api)
                        ├─ workflow["103"]["inputs"]["text"] = "把背景换成海边"
                        └─ api.upload_image(bytes) → workflow["41"]["inputs"]["image"]
```

### 4.3 生视频

```
用户: "rh 生视频 一只猫在草地上追蝴蝶"
    │
    ▼
generate_video()
    │
    ├─ ev.text = "一只猫在草地上追蝴蝶"
    ├─ ev.image_id = None → task_type = TEXT2VIDEO
    │
    └─ _do_generate()
         ├─ route() → PipelineDef(name="wan2.2_text2video", backend="comfyui")
         ├─ check_point(ev, 15) → ✅
         └─ execute_generation()
              └─ ComfyUIBackend.execute()
                   └─ wan_text2video_mapper(request, workflow, api)
                        ├─ workflow["37"]["inputs"]["text"] = "一只猫在草地上追蝴蝶"
                        ├─ workflow["44"]["inputs"]["value"] = 720
                        ├─ workflow["34"]["inputs"]["value"] = 1280
                        └─ workflow["33"]["inputs"]["value"] = 5
    │
    ▼
bot.send(MessageSegment.video(result.data))
```

### 4.4 生音乐

```
用户: "rh 生音乐 轻松愉快的背景音乐"
    │
    ▼
generate_music()
    │
    ├─ task_type = MUSIC
    └─ _do_generate()
         ├─ route() → PipelineDef(name="ace_step1.5", backend="comfyui")
         ├─ check_point(ev, 2) → ✅
         └─ execute_generation()
              └─ ace_step_mapper(request, workflow, api)
                   ├─ workflow["131"]["inputs"]["text"] = "轻松愉快的背景音乐"
                   └─ workflow["130"]["inputs"]["text"] = ""
    │
    ▼
bot.send(MessageSegment.record(result.data))
```

### 4.5 生语音

```
用户: "rh 生语音 欢迎使用RH_ComfyUI"
    │
    ▼
generate_speech()
    │
    ├─ task_type = SPEECH
    └─ _do_generate()
         ├─ route() → PipelineDef(name="IndexTTS2", backend="comfyui")
         ├─ check_point(ev, 2) → ✅
         └─ execute_generation()
              └─ index_tts2_mapper(request, workflow, api)
                   └─ workflow["14"]["inputs"]["value"] = "欢迎使用RH_ComfyUI"
    │
    ▼
bot.send(MessageSegment.record(result.data))
```

---

## 五、错误处理与降级

### 路由阶段错误

```
route(request)
    │
    ├─ 用户指定模型不存在 → 警告日志 → 回退自动选择
    ├─ 用户指定模型不可用 → 警告日志 → 回退自动选择
    ├─ 所有模型不可用 → ModelUnavailableError
    │     → ai_return("错误：...") + bot.send("❌ ...")
    │     → return None（终止流程）
    └─ AI 推荐失败 → 警告日志 → 回退优先级兜底
```

### 积分阶段错误

```
check_point(ev, point)
    │
    ├─ 积分充足 → ✅ 自动扣除
    └─ 积分不足 → ❌
          → ai_return("错误：积分不足")
          → bot.send("❌ 积分不足！...")
          → return None（终止流程）
```

### 执行阶段错误

```
execute_generation(request, pipeline)
    │
    ├─ 后端未注册 → RuntimeError
    ├─ 工作流文件不存在 → RuntimeError
    ├─ ComfyUI 连接失败 → Exception
    ├─ 生成超时 → Exception
    └─ 任何异常
          → logger.exception()
          → ai_return("错误：生成失败 - ...")
          → bot.send("❌ 生成失败: ...")
          → return None（终止流程）
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
        concurrency = RHCOMFYUI_CONFIG.get_config("Max_Concurrency").data
        _generation_semaphore = asyncio.Semaphore(concurrency)
    return _generation_semaphore
```

- 所有后端（ComfyUI、BLT、RH App）**共享同一个 Semaphore**
- 并发数由配置 `Max_Concurrency` 控制（默认 1）
- 超出并发限制的请求会在 `async with sem:` 处等待

### 执行流程中的限流点

```
execute_generation()
    │
    ├─ 获取 Semaphore（可能等待）
    │
    ├─ async with sem:  ← 限流点
    │     └─ backend.execute(request, pipeline)
    │
    └─ 释放 Semaphore（自动）
```
