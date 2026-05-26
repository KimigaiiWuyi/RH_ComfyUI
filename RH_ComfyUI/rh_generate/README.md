# rh_generate — 统一 AI 生成命令模块

## 模块概述

`rh_generate` 是 RH_ComfyUI 插件的**核心命令处理模块**，合并了原 `rh_draw`、`rh_video`、`rh_audio` 三个独立模块。负责接收用户的生成命令（生图/改图/生视频/生音乐/生语音），解析参数，路由到合适的 Pipeline，检查积分，执行生成，并将结果发送给用户。

## 文件结构

```
rh_generate/
├── __init__.py       # 命令处理器（SV 触发器 + 生成执行流程）
└── _knowledge.py     # AI 知识库注册（将 Pipeline 信息注册为 AI 可检索知识）
```

## 核心组件

### 1. 命令处理器 [`__init__.py`](RH_ComfyUI/rh_generate/__init__.py:1)

#### SV 实例

```python
sv_gen = SV("AI生成")
```

#### 注册的命令

| 命令关键词 | 处理函数 | to_ai | 说明 |
|-----------|---------|-------|------|
| `生图` | [`generate_image()`](RH_ComfyUI/rh_generate/__init__.py:110) | ✅ | 文生图/图生图 |
| `改图` / `编辑图片` / `图片编辑` | [`edit_image()`](RH_ComfyUI/rh_generate/__init__.py:164) | ✅ | 图片编辑 |
| `生视频` / `生成视频` | [`generate_video()`](RH_ComfyUI/rh_generate/__init__.py:214) | ✅ | 文生视频/图生视频 |
| `生音乐` / `生成音乐` | [`generate_music()`](RH_ComfyUI/rh_generate/__init__.py:259) | ✅ | 音乐生成 |
| `生语音` / `生成语音` | [`generate_speech()`](RH_ComfyUI/rh_generate/__init__.py:295) | ✅ | 语音合成 |
| `模型列表` | [`list_models()`](RH_ComfyUI/rh_generate/__init__.py:321) | ❌ | 列出所有可用模型 |
| `模型详情` | [`model_detail()`](RH_ComfyUI/rh_generate/__init__.py:350) | ❌ | 查看模型详情 |

#### 统一生成执行流程 [`_do_generate()`](RH_ComfyUI/rh_generate/__init__.py:55)

所有生成命令共享同一个执行流程：

```
用户命令 → 解析参数 → _do_generate()
    ├── 1. route(request)         → 智能路由选择 Pipeline
    ├── 2. check_point(ev, cost)  → 积分检查与扣除
    └── 3. execute_generation()   → 执行生成
```

#### 任务类型自动推断

根据用户是否附带图片自动推断任务类型：

| 场景 | 命令 | 有图片 | 推断任务类型 |
|------|------|--------|------------|
| 文生图 | `生图` | ❌ | `TEXT2IMAGE` |
| 图生图 | `生图` | ✅ | `IMAGE2IMAGE` |
| 图片编辑 | `改图` | ✅（必须） | `IMAGE_EDIT` |
| 文生视频 | `生视频` | ❌ | `TEXT2VIDEO` |
| 图生视频 | `生视频` | ✅ | `IMAGE2VIDEO` |
| 音乐生成 | `生音乐` | ❌ | `MUSIC` |
| 语音合成 | `生语音` | ❌ | `SPEECH` |

#### 透明图片处理 [`_flatten_transparent_image_to_white()`](RH_ComfyUI/rh_generate/__init__.py:37)

在图片编辑前，自动将透明图片合成到白色背景，避免编辑模型处理透明通道时出错。

### 2. AI 知识库注册 [`_knowledge.py`](RH_ComfyUI/rh_generate/_knowledge.py:1)

[`register_pipeline_knowledge()`](RH_ComfyUI/rh_generate/_knowledge.py:11) 函数在启动时将所有 Pipeline 信息注册为 AI 知识库条目：

```python
for p in pipeline_registry.all_pipelines():
    ai_entity(KnowledgePoint(
        id=f"rh_comfyui_pipeline_{p.name}",
        plugin="RH_ComfyUI",
        title=f"{p.display_name} - {p.task_type.value}",
        content=f"...基本信息、描述、详细说明、使用方式...",
        tags=["RH_ComfyUI", "AIGC", p.task_type.value, p.name, p.display_name],
    ))
```

这使得 AI 在 RAG 检索时能找到各模型的详细信息，从而做出更好的模型推荐。

## 消息流转（以「生图」为例）

```
用户: "rh 生图 qwen 一只可爱的猫咪"
    │
    ▼
sv_gen.on_command("生图") → generate_image()
    │
    ├─ 1. parse_model_from_prompt("qwen 一只可爱的猫咪", TEXT2IMAGE)
    │     → model="qwen_2512", prompt="一只可爱的猫咪"
    │
    ├─ 2. GenerationRequest(task_type=TEXT2IMAGE, prompt="一只可爱的猫咪", model="qwen_2512")
    │
    ├─ 3. _do_generate(request, ev, bot)
    │     ├─ route(request) → PipelineDef(name="qwen_2512", backend="comfyui", ...)
    │     ├─ check_point(ev, 2) → 扣除2积分
    │     └─ execute_generation(request, pipeline) → GenerationResult(data=PNG bytes)
    │
    └─ 4. bot.send(convert_img(result.data))
```

## 与其他模块的关系

```
rh_generate ──→ utils/core/parser.py     （解析模型名）
            ──→ utils/core/router.py     （智能路由）
            ──→ utils/core/executor.py   （执行生成）
            ──→ utils/core/pipeline.py   （Pipeline 注册表）
            ──→ utils/core/request.py    （请求/响应模型）
            ──→ utils/points.py          （积分检查）
            ──→ rh_generate/_knowledge.py（AI 知识库注册）
```
