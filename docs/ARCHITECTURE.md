# RH_ComfyUI 项目架构文档

> **对应代码版本**：v2.x（声明式 Pipeline + Adapter 后端抽象 + Seedance 多供应商 + rh_models Web API）
>
> 本文档基于现行代码重写。旧版的「RHCOMFYUI_CONFIG」「7 种 TaskType」「BLT 后端」「三层 BackendAdapter/Adapter/ComfyUIAdapter」等描述已不再适用；具体见 [迁移对照表](#迁移对照表旧架构-→-新架构)。

## 目录

- [RH_ComfyUI 项目架构文档](#rh_comfyui-项目架构文档)
  - [目录](#目录)
  - [一、项目概述](#一项目概述)
  - [二、整体架构图](#二整体架构图)
  - [三、分层架构详解](#三分层架构详解)
    - [3.1 命令层（Command Layer）](#31-命令层command-layer)
    - [3.2 核心引擎层（Core Engine Layer）](#32-核心引擎层core-engine-layer)
    - [3.3 后端抽象层（Backend Abstraction Layer）](#33-后端抽象层backend-abstraction-layer)
    - [3.4 资源层（Resource Layer）](#34-资源层resource-layer)
    - [3.5 基础设施层（Infrastructure Layer)](#35-基础设施层infrastructure-layer)
    - [3.6 Web API 层（rh_models）](#36-web-api-层rh_models)
  - [四、消息流转机制](#四消息流转机制)
  - [五、核心设计模式](#五核心设计模式)
  - [六、模块职责速查表](#六模块职责速查表)
  - [七、数据流向图](#七数据流向图)
  - [八、配置管理体系](#八配置管理体系)
  - [九、扩展指南](#九扩展指南)
  - [迁移对照表（旧架构 → 新架构）](#迁移对照表旧架构--新架构)

---

## 一、项目概述

RH_ComfyUI 是一个基于 GsCore 机器人框架的 **AIGC 统一生成插件**（v2.0.0），支持通过聊天机器人命令、AI Agent 或 Web API 调用来完成图片生成、图片编辑、视频生成、音乐创作和语音合成等多种 AIGC 任务。

**核心设计理念：**
- **多后端统一抽象**：通过 `Adapter` 基类统一 ComfyUI、GPT-Image2 / OpenAI 兼容、RunningHub、MiniMax、MiMo TTS、Seedance 等多种后端
- **声明式 Pipeline 架构**：通过 YAML 文件定义工作流 + 输入输出端口规格 + 能力声明，无需修改代码即可添加新模型
- **智能路由**：按"输出模态"圈桶 + 输入档案匹配（图片数量/音视频参考），支持用户指定、AI 推荐、优先级兜底
- **Seedance 多供应商**：自动在 ARK / 聚合网关 / RunningHub 之间负载均衡 + 熔断
- **积分经济系统**：通过积分控制生成成本，支持管理员管理与失败自动退还

**技术栈：**
- Python 3.10+
- GsCore 插件框架
- FastAPI（rh_models Web API 通过框架 `app_life.app` 挂载）
- SQLModel / SQLAlchemy（数据库）
- httpx / aiohttp / websockets（HTTP/WS 通信）
- PIL / Pillow（图片处理）
- PyYAML（Pipeline YAML 解析）

---

## 二、整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     用户 / AI Agent / Web API                       │
│         "rh 生图 qwen 一只可爱的猫咪" / generate_image() / GET     │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
       ┌───────────────────────┼─────────────────────────────────┐
       ▼                       ▼                                 ▼
┌─────────────────┐  ┌────────────────────┐         ┌──────────────────┐
│   命令层         │  │  rh_models          │         │  rh_admin        │
│  rh_generate    │  │  (命令+FastAPI+AI)  │         │  (积分管理)      │
│  to_ai=...      │  │  /RH_ComfyUI/models │         │  @ai_tools       │
└────────┬────────┘  └─────────┬──────────┘         └─────────┬────────┘
         │                     │                              │
         └─────────────────────┼──────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    核心引擎层 (Core Engine)                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │  Parser   │ │  Router  │ │ Executor │ │ Pipeline │ │  Types   │ │
│  │ 命令解析  │ │ 智能路由 │ │ 统一执行 │ │ Registry │ │ PortSpec │ │
│  │          │ │ (输入档案 │ │(限流+落盘)│ │ (YAML)   │ │ MediaRef │ │
│  │          │ │  +能力)  │ │          │ │          │ │ NodeOut  │ │
│  └─────┬────┘ └─────┬────┘ └─────┬────┘ └─────┬────┘ └──────────┘ │
│        │            │            │             │                   │
│  ┌─────┴────────────┴────────────┴─────────────┴───────────────┐  │
│  │         request.py (GenerationRequest / GenerationResult)  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  后端抽象层 (Backend Abstraction)                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                     Adapter (ABC)                            │  │
│  │  name / check_available / capabilities / execute             │  │
│  └────────┬─────────┬─────────┬─────────┬─────────┬───────────┘  │
│           ▼         ▼         ▼         ▼         ▼              │
│  ┌────────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐ │
│  │  ComfyUI   │ │ GPT-    │ │  RH App │ │ MiniMax │ │   MiMo   │ │
│  │  Adapter   │ │ Image2  │ │ Adapter │ │ Adapter │ │  Adapter │ │
│  │ (WS+JSON) │ │ (OpenAI) │ │ (OpenAPI│ │ (REST)  │ │  (REST)  │ │
│  └────────────┘ └─────────┘ └─────────┘ └─────────┘ └──────────┘ │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Seedance Adapter (multi-provider)                           │  │
│  │    ARK  / Gateway  / RunningHub (负载均衡 + 熔断)            │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    资源层 (Resource Layer)                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │  Pipeline YAML   │  │  Workflow JSON   │  │  RESOURCE_PATH   │   │
│  │  (节点定义)      │  │  (ComfyUI工作流) │  │  (路径+落盘)     │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                基础设施层 (Infrastructure)                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │  SERVICE_CONFIG  │  │  PLUGIN_CONFIG   │  │  database/models │   │
│  │  (上游服务连接)  │  │  (并发+积分)     │  │  (RHBind 积分)   │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 三、分层架构详解

### 3.1 命令层（Command Layer）

命令层是用户与系统的交互入口，负责接收消息、解析参数、调用核心引擎并返回结果。

#### [`rh_generate`](RH_ComfyUI/rh_generate/__init__.py:1) — 生成命令模块

**职责：** 接收所有 AIGC 生成命令，是插件的核心业务入口。

**注册的触发器：**

```python
sv_gen = SV("AI生成")

@sv_gen.on_command("生图", block=True, to_ai="...")
async def generate_image(bot: Bot, ev: Event) -> None: ...

@sv_gen.on_command(("改图", "编辑图片", "图片编辑"), block=True, to_ai="...")
async def edit_image(bot: Bot, ev: Event) -> None: ...

@sv_gen.on_command(("生视频", "生成视频"), block=True, to_ai="...")
async def generate_video(bot: Bot, ev: Event) -> None: ...

@sv_gen.on_command(("生音乐", "生成音乐"), block=True, to_ai="...")
async def generate_music(bot: Bot, ev: Event) -> None: ...

@sv_gen.on_command(("生语音", "生成语音"), block=True, to_ai="...")
async def generate_speech(bot: Bot, ev: Event) -> None: ...
```

**内部通用执行函数 [`_do_generate()`](RH_ComfyUI/rh_generate/__init__.py:57)：**

```python
async def _do_generate(request: GenerationRequest, ev: Event, bot: Bot, *, on_progress=None) -> Optional[GenerationResult]:
    # 1. 智能路由
    try:
        node = await route(request)
    except ModelUnavailableError as e:
        ai_return(f"错误：{e.reason}")
        await bot.send(f"❌ {e.reason}")
        return None

    # 2. 积分检查
    success, msg = await check_point(ev, node.point_cost)
    if not success:
        ai_return(f"错误：积分不足，需要{node.point_cost}积分")
        await bot.send(msg)
        return None
    await bot.send(f"{msg}\n🎯 使用模型: {node.display_name}")

    # 3. 进度回调(包装后透传给 executor)
    async def _wrapped_progress(event):
        await bot.send(f"⏳ {event.message}")
        if on_progress is not None:
            try: await on_progress(event)
            except: pass

    # 4. 执行
    try:
        result = await execute_generation(request, node, on_progress=_wrapped_progress)
        ai_return(f"生成完成，使用模型: {node.display_name}")
        return result
    except Exception as e:
        # 失败自动退还积分
        await RHBind.add_point(ev.user_id, ev.bot_id, node.point_cost)
        ...
```

**关键变化：**
- **失败自动退还积分**：生成失败时立即调用 `RHBind.add_point()` 退还本次消费的积分
- **`on_progress` 包装**：执行前包一层"bot.send 进度消息"的回调
- **`_do_generate` 默认消费 `node.point_cost`**（由 YAML 声明），不再依赖 `points.py` 中的全局常量

**`to_ai=` 机制：** 每个命令都通过 `to_ai=` 参数注册为 AI 工具，AI Agent 可直接调用。

#### [`rh_admin`](RH_ComfyUI/rh_admin/__init__.py:1) — 积分管理模块

**职责：** 提供积分的增删查功能，同时支持命令和 AI Tools。

**核心函数（同时注册为 `@ai_tools`）：**
- [`add_user_points()`](RH_ComfyUI/rh_admin/commands.py:107) — 增加积分（需管理员权限）
- [`deduct_user_points()`](RH_ComfyUI/rh_admin/commands.py:141) — 扣除积分（需管理员权限；积分不足时扣除全部剩余）
- [`query_user_points()`](RH_ComfyUI/rh_admin/commands.py:184) — 查询积分

#### [`rh_agent`](RH_ComfyUI/rh_agent/__init__.py:1) — AI 代理注册模块

**职责：** 注册 AIGC 创作能力代理画像，让 AI Agent Mesh 自动路由 AIGC 任务。

**关键点：** 模块在 `import` 时**立即**调用 `register_rh_aigc_agent()`，所以不需要 `init_pipeline_system` 显式触发。

#### [`rh_help`](RH_ComfyUI/rh_help/__init__.py:1) — 帮助模块

**职责：** 提供帮助信息，注册到全局帮助一览（`register_help("RH_ComfyUI", ...)`）。

### 3.2 核心引擎层（Core Engine Layer）

核心引擎层是整个系统的**心脏**，实现了「声明式 Pipeline 架构」的核心抽象。

#### [`types.py`](RH_ComfyUI/utils/core/types.py:1) — 核心类型系统（新增）

| 类型 | 作用 |
|------|------|
| `PortType` | 端口类型枚举（覆盖 STRING/TEXT/INTEGER/NUMBER/BOOLEAN/ENUM/LIST/IMAGE/VIDEO/AUDIO/CONTENT/OUTPUT_*） |
| `PortSpec` | 端口规格（type/required/default/minimum/maximum/values/item_type/min_items/max_items/mime_types） |
| `MediaKind` | 媒体类型枚举（IMAGE/VIDEO/AUDIO） |
| `MediaRef` | 媒体引用（data 或 url + 自动嗅探 mime_type） |
| `ContentItem` / `ContentItemType` | 有序多模态内容项（Seedance 2.0 风格） |
| `CapabilityManifest` | Adapter/Node 能力声明（驱动 Router） |
| `ProgressEvent` | 进度上报事件（stage/percent/message/extra） |
| `NodeOutput` | 统一节点输出（取代旧 GenerationResult 主输出 + 扩展字段） |

便利构造器：`image_ref` / `video_ref` / `audio_ref` / `text_item` / `image_item` / `video_item` / `audio_item` / `draft_task_item`。

#### [`request.py`](RH_ComfyUI/utils/core/request.py:1) — 统一请求/响应模型

定义了覆盖所有 AIGC 任务的统一数据模型：

- [`TaskType`](RH_ComfyUI/utils/core/request.py:54) — **4 种任务类型枚举**（IMAGE/VIDEO/MUSIC/SPEECH，按"输出模态"分类）
- [`OutputType`](RH_ComfyUI/utils/core/request.py:72) — 3 种输出类型枚举（IMAGE/VIDEO/AUDIO）
- [`normalize_task_type()`](RH_ComfyUI/utils/core/request.py:39) — 旧式细分类型(text2image/image2image 等)→当前 TaskType 归一化
- [`GenerationRequest`](RH_ComfyUI/utils/core/request.py:110) — 统一请求模型（30+ 字段）
- [`GenerationResult`](RH_ComfyUI/utils/core/request.py:299) — 统一响应模型（含 outputs/usage/raw 扩展字段，兼容旧 API）

**`GenerationRequest` 关键字段矩阵：**

| 字段 | image | video | music | speech |
|------|:-----:|:-----:|:-----:|:------:|
| prompt | ✅ | ✅ | ✅ | ✅ |
| negative_prompt | ⭕ | ⭕ | ✅ 歌词 | — |
| images | ⭕ 1+ | ⭕ 1+ | — | — |
| video_refs | — | ⭕ | — | — |
| audio_refs | — | ⭕ | — | — |
| ordered_content | — | ⭕ | — | — |
| reference_audio | — | — | — | ⭕ |
| mood | — | — | — | ⭕ |
| voice_id | — | — | — | ⭕ |
| width/height | ⭕ | ⭕ | — | — |
| ratio/resolution | — | ⭕ | — | — |
| duration | — | ✅ | — | — |
| seed | ⭕ | ⭕ | — | — |
| generate_audio | — | ⭕ | — | — |
| watermark | ⭕ | ⭕ | — | — |
| camera_fixed | — | ⭕ | — | — |
| return_last_frame | — | ⭕ | — | — |
| service_tier | — | ⭕ | — | — |
| model | ⭕ | ⭕ | ⭕ | ⭕ |
| speed / language_boost | — | — | — | ⭕ |
| params | ⭕ | ⭕ | ⭕ | ⭕ |

> **关键设计变化**：旧版细分任务类型（image2image/text2image/image_edit 等）已被收编为 4 种 TaskType + NodeDef 的 `inputs` 端口规格约束。具体形态（首尾帧 / 多模态 / 编辑）由 `inputs.images.min_items/max_items` 决定。

#### [`pipeline.py`](RH_ComfyUI/utils/core/pipeline.py:1) — Pipeline 注册表

- [`NodeDef`](RH_ComfyUI/utils/core/pipeline.py:66) — Pipeline 定义数据类
- [`PipelineDef`](RH_ComfyUI/utils/core/pipeline.py:121) — `NodeDef` 的向后兼容别名
- [`PipelineRegistry`](RH_ComfyUI/utils/core/pipeline.py:129) — 注册表（从 YAML 自动加载）
- [`pipeline_registry`](RH_ComfyUI/utils/core/pipeline.py:373) — 全局单例

**NodeDef 新增字段：**
- `backend_model` / `backend_models` / `provider`：多供应商支持
- `inputs` / `outputs`：类型化端口（驱动 Router 输入档案匹配）
- `capabilities`：能力声明（priority/mode/output_mime/supported_params/supported_tasks/fallback）

#### [`router.py`](RH_ComfyUI/utils/core/router.py:1) — 智能路由器

[`route(request)`](RH_ComfyUI/utils/core/router.py:137) — 五级路由策略：

1. **用户显式指定 model** → 精确匹配 / 输入感知的部分名匹配，并校验可用性
2. **输入档案匹配** → 同 TaskType 桶内，按 `inputs` 端口过滤兼容当前输入形状（图片数量 min/max、是否接受音/视频参考）的节点
3. **可用性过滤** → 对候选逐个 `check_available()`，剔除未配置后端
4. **AI Agent 推荐**（可选） → 临时 AI Agent 从可用候选中选择
5. **优先级兜底** → 按 `capabilities.priority` 排序，同优先级随机

#### [`executor.py`](RH_ComfyUI/utils/core/executor.py:1) — 统一执行器

[`execute_generation(request, node, *, on_progress)`](RH_ComfyUI/utils/core/executor.py:110) — 唯一执行路径：

1. `backend_registry.get(node.backend)` → 取 Adapter
2. `_get_semaphore()` 全局限流
3. `adapter.execute(request, node, on_progress=...)` → 拿到 `NodeOutput`
4. `_save_output()` 落盘到 `OUTPUT_PATH/<task_type>/<ts>.<ext>`
5. 包装为 `GenerationResult`（兼容旧 API）返回

#### [`parser.py`](RH_ComfyUI/utils/core/parser.py:1) — 命令解析器

- [`parse_model_from_prompt(text, task_type)`](RH_ComfyUI/utils/core/parser.py:44) — 从用户输入提取模型 token（**只返回 token，不解析为具体 NodeDef**；由 Router 做输入感知的最终匹配）
- [`parse_mood_from_prompt(text)`](RH_ComfyUI/utils/core/parser.py:121) — 从文本开头解析情绪标签 `[情绪]` / `[情绪:xxx]`

### 3.3 后端抽象层（Backend Abstraction Layer）

#### [`base.py`](RH_ComfyUI/utils/backends/base.py:1) — Adapter 抽象基类

所有后端必须实现五个成员：

| 成员 | 说明 |
|------|------|
| `name` | 后端唯一标识（类属性） |
| `check_available()` | 检查后端是否可用（配置/连接） |
| `get_unavailable_reason()` | 返回不可用原因 |
| `capabilities()` | 声明能力（驱动 Router） |
| `execute()` | 执行生成任务，返回 `NodeOutput` |

`Backend` 是 `Adapter` 的向后兼容别名。

#### 六个后端实现

| 后端 | 文件 | 标识 | 连接方式 | 映射模式 | 特点 |
|------|------|------|---------|---------|------|
| ComfyUI | [`comfyui/`](RH_ComfyUI/utils/backends/comfyui/) | `comfyui` | WebSocket + HTTP | 声明式 / 编程式 | 支持工作流 JSON + `set_workflow_override` 动态切换 + RunningHub 代理 |
| GPT-Image2 | [`gpt_image2/`](RH_ComfyUI/utils/backends/gpt_image2/) | `gpt-image-2` | HTTP REST | 仅编程式 | OpenAI 兼容（OpenAI / OneAPI / NewAPI / OpenRouter / BLT / SiliconFlow / Ollama） |
| RH App | [`rh_app/`](RH_ComfyUI/utils/backends/rh_app/) | `rh_app` | HTTP REST | 声明式 | RunningHub 原生 AI 应用（OpenAPI v2） |
| MiniMax | [`minimax/`](RH_ComfyUI/utils/backends/minimax/) | `minimax` | HTTP REST | 仅编程式 | 图像 + T2A 异步语音 + 音色克隆 |
| MiMo | [`mimo/`](RH_ComfyUI/utils/backends/mimo/) | `mimo` | HTTP REST | 仅编程式 | XiaoMi TTS（预置 / 设计 / 复刻） |
| Seedance | [`seedance/`](RH_ComfyUI/utils/backends/seedance/) | `seedance` | HTTP REST | 仅编程式 | 多供应商 + 负载均衡 + 熔断 |

#### Seedance 多供应商子层

[`seedance/`](RH_ComfyUI/utils/backends/seedance/) 子模块独立设计：

- [`spec.py`](RH_ComfyUI/utils/backends/seedance/spec.py:1) — `VideoTaskShape`（T2V/I2V/FLF/Multimodal/Video_Edit/Video_Extend）、`MediaRole`、`VideoGenSpec` 等供应商无关的归一化数据类
- [`classify.py`](RH_ComfyUI/utils/backends/seedance/classify.py:1) — `classify_video_spec(request)` 按输入自动判定任务形态
- [`provider.py`](RH_ComfyUI/utils/backends/seedance/provider.py:1) — `SeedanceProvider` ABC + `NormalizedTask` + `SeedanceProviderError` + `normalize_usage()`
- [`providers/`](RH_ComfyUI/utils/backends/seedance/providers/) — 三个供应商驱动（ARK / Gateway / RunningHub）
- [`registry.py`](RH_ComfyUI/utils/backends/seedance/registry.py:1) — 供应商选择器 + 负载均衡（round_robin / weighted / least_failures）+ 熔断器
- [`_debug.py`](RH_ComfyUI/utils/backends/seedance/_debug.py:1) — Dry-Run / 日志脱敏工具

#### [`mappers/`](RH_ComfyUI/utils/mappers/) — 参数映射函数

将 `GenerationRequest` 转换为各后端所需的参数格式。11 个 mapper 覆盖所有任务类型。

### 3.4 资源层（Resource Layer）

#### [`resource/RESOURCE_PATH.py`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:1)

路径管理 + 工作流加载 + 目录初始化。**模块导入时自动执行 `init_dir()`**，把内置 workflow/pipelines 复制到运行时目录。

**目录结构变化（相对旧版）：**

| 旧版目录 | 新版目录 | 说明 |
|---------|---------|------|
| `text2image/` + `image2image/` + `image_edit/` | `imagegen/` | 统一为图片生成根目录；具体形态由 NodeDef 决定 |
| `text2video/` + `image2video/` | `videogen/` | 统一为视频生成根目录 |

### 3.5 基础设施层（Infrastructure Layer）

#### 配置拆分

| 旧 | 新 |
|----|-----|
| `RHCOMFYUI_CONFIG`（单实例） | `SERVICE_CONFIG`（上游服务连接）+ `PLUGIN_CONFIG`（插件行为） |

- [`SERVICE_CONFIG`](RH_ComfyUI/rh_config/comfyui_config.py:22) — 上游服务连接（API Key / BaseURL / Seedance 供应商），按 `GsDivider` 分组
- [`PLUGIN_CONFIG`](RH_ComfyUI/rh_config/comfyui_config.py:28) — 并发上限 / 初始积分 / 业务积分消耗

#### [`database/models.py`](RH_ComfyUI/utils/database/models.py:1) — 数据库

`RHBind` 积分表（继承 `Bind`，增加 `point: int` 字段），提供积分的增删改查，注册到 Web 控制台（`SsPushAdmin`）。

#### [`points.py`](RH_ComfyUI/utils/points.py:1) — 积分检查

[`check_point()`](RH_ComfyUI/utils/points.py:21) 函数，检查积分是否充足并自动扣除。

> **注意**：旧版导出 5 个 `*_Point` 模块常量；新版 `_do_generate()` 实际使用 `node.point_cost`（YAML 声明），这些模块常量仅为历史兼容保留。

### 3.6 Web API 层（rh_models）

[`rh_models/`](RH_ComfyUI/rh_models/) 是命令 + Web API + AI 工具的三合一模块：

- [`__init__.py`](RH_ComfyUI/rh_models/__init__.py:1) — 命令触发器（`模型列表` / `模型清单` / `可用模型`，支持按任务类型过滤）
- [`api.py`](RH_ComfyUI/rh_models/api.py:1) — 数据聚合：`ModelEntry` / `build_model_catalog()` / `build_backend_summary()` / `get_models_by_task()`
- [`utils.py`](RH_ComfyUI/rh_models/utils.py:1) — `format_text()`（人类可读）+ `ai_list_models()`（AI 工具）
- [`webapi.py`](RH_ComfyUI/rh_models/webapi.py:1) — FastAPI 路由

**FastAPI 路由（挂在 `gsuid_core.app_life.app` 上）：**

| 路由 | 说明 |
|------|------|
| `GET /RH_ComfyUI/models` | 全量模型清单（按任务类型分组，含后端可用性） |
| `GET /RH_ComfyUI/models/{task_type}` | 按任务类型过滤（接受中英文别名：`图片`/`视频`/`音乐`/`语音`/`tts`） |
| `GET /RH_ComfyUI/models/summary` | 后端可用性摘要（总览面板用） |

**关键点**：`__init__.py` 通过 `from . import webapi` 触发路由挂载，所以 `RH_ComfyUI/__init__.py` 顶部还需要 `from . import rh_models` 来确保路由在启动钩子之前就生效。

---

## 四、消息流转机制

### 4.1 完整消息流转图

```
用户发送消息: "rh 生图 qwen 一只可爱的猫咪"
    │
    ▼
┌─────────────────────────────────────────────────┐
│ GsCore 框架消息分发                               │
│  1. 匹配 force_prefix "rh"                       │
│  2. 匹配 on_command "生图"                        │
│  3. 调用 generate_image(bot, ev)                  │
│     ev.text = "qwen 一只可爱的猫咪"               │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ① Parser 解析                                    │
│  parse_model_from_prompt("qwen 一只可爱的猫咪",   │
│                           TaskType.IMAGE)        │
│  → model = "qwen" (token,不解析为具体 NodeDef)   │
│  → prompt = "一只可爱的猫咪"                      │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ② 构建 GenerationRequest                         │
│  GenerationRequest(                              │
│    task_type = IMAGE,                            │
│    prompt = "一只可爱的猫咪",                     │
│    model = "qwen",                              │
│    width = 720, height = 1280                    │
│  )                                               │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ③ Router 智能路由                                │
│  route(request)                                  │
│  → Step 1: 用户指定 "qwen" → 模糊匹配 qwen_2512   │
│  → Step 2: 输入档案过滤 (0 张图)                  │
│  → Step 3: 检查 ComfyUI 后端可用 → ✅            │
│  → 返回 NodeDef(name="qwen_2512", backend="comfyui", point_cost=2) │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ④ 积分检查                                       │
│  check_point(ev, point_cost=2)                   │
│  → RHBind.deduct_point() → ✅ 扣除2积分          │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑤ Executor 统一执行                              │
│  execute_generation(request, node, on_progress=…) │
│  → 获取 Semaphore（并发限制）                     │
│  → adapter = backend_registry.get("comfyui")     │
│  → ComfyUIAdapter.execute() → NodeOutput         │
│  → 落盘到 OUTPUT_PATH/image/<ts>.png              │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑥ ComfyUIAdapter.execute()                       │
│  → 加载 workflow JSON (qwen_2512.json)           │
│  → 声明式映射: prompt→108.inputs.text             │
│               width→107.inputs.width             │
│               height→107.inputs.height           │
│  → api.generate_image_by_prompt(workflow)        │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑦ ComfyUIAPI 通信                               │
│  → WebSocket 连接                                │
│  → POST /prompt 提交工作流                        │
│  → WebSocket 监听进度事件                         │
│  → GET /history 获取结果                          │
│  → GET /view 下载图片                             │
│  → 返回 PIL.Image                                │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑧ 返回结果                                       │
│  GenerationResult(                               │
│    output_type = IMAGE,                          │
│    data = PNG bytes,                             │
│    pipeline_used = "qwen_2512",                  │
│    cost_points = 2                               │
│  )                                               │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑨ 发送结果                                       │
│  bot.send("✅ 图片生成完成！")                    │
│  bot.send(await convert_img(result.data))        │
└─────────────────────────────────────────────────┘
```

### 4.2 以「生图」为例的详细流程

**代码路径：**

```
rh_generate/__init__.py:generate_image()
  ├── core/parser.py:parse_model_from_prompt()     # 解析模型 token
  ├── core/request.py:GenerationRequest()           # 构建请求
  ├── rh_generate/__init__.py:_do_generate()        # 通用执行流程
  │     ├── core/router.py:route()                  # 智能路由（输入档案 + 可用性 + 优先级）
  │     ├── utils/points.py:check_point()           # 积分检查
  │     │     └── database/models.py:RHBind.deduct_point()
  │     └── core/executor.py:execute_generation()   # 统一执行
  │           └── backends/comfyui/executor.py:ComfyUIAdapter.execute()
  │                 ├── resource/RESOURCE_PATH.py:load_workflow()
  │                 ├── backends/comfyui/executor.py:_apply_declarative_mappings()
  │                 └── backends/comfyui/api.py:ComfyUIAPI.generate_image_by_prompt()
  │                       ├── api.py:queue_prompt()
  │                       ├── api.py:track_progress()
  │                       └── api.py:get_images()
  └── gsuid_core:bot.send(convert_img(result.data))
```

### 4.3 AI Agent 调用流程

当 AI Agent 调用 `generate_image` 工具时：

```
AI Agent 决定调用 generate_image(text="一只可爱的猫咪", image_id=None)
    │
    ▼
GsCore MockBot 拦截
    │
    ▼
generate_image(bot=MockBot, ev=MockEvent)
    │  ev.text = "一只可爱的猫咪"
    │  ev.image_id = None (或 "img_xxx")
    │
    ▼
（同上述流程，但 bot.send() 被 MockBot 拦截）
    │
    ▼
图片通过 RM.register() 注册，返回资源 ID
文字通过 ai_return() 返回给 AI
    │
    ▼
AI Agent 决定是否调用 send_message_by_ai(image_id="img_xxx")
```

---

## 五、核心设计模式

### 5.1 声明式 Pipeline 架构

**核心思想：** 将模型的元数据、参数映射规则、输入输出端口、能力声明从代码中抽离到 YAML 文件，实现「零代码添加新模型」。

```yaml
# 只需一个 YAML 文件即可添加新模型
name: "my_new_model"
display_name: "我的新模型"
task_type: image
backend: comfyui
point_cost: 3

capabilities:
  priority: 50
  mode: sync

inputs:
  prompt:
    type: text
    required: true
  width:
    type: integer
    default: 1024
    minimum: 256
    maximum: 2048

outputs:
  image:
    type: output_image

mappings:
  - source: prompt
    target: "1.inputs.text"
  - source: width
    target: "2.inputs.width"
    default: 1024
```

**两种映射模式：**
- **declarative**（声明式）：YAML 中定义 `source → target` 映射规则
- **programmatic**（编程式）：Python 函数处理复杂逻辑（如图片上传、工作流覆盖、Seedance 多供应商分发）

### 5.2 策略模式（Adapter 抽象）

```python
class Adapter(ABC):
    name: str
    @abstractmethod
    async def check_available(self) -> bool: ...
    @abstractmethod
    async def get_unavailable_reason(self) -> str: ...
    @abstractmethod
    def capabilities(self) -> CapabilityManifest: ...
    @abstractmethod
    async def execute(self, request, node, *, on_progress=None) -> NodeOutput: ...

class ComfyUIAdapter(Adapter): ...     # WebSocket + 工作流 JSON
class GPTImage2Adapter(Adapter): ...   # OpenAI 兼容 REST
class RHAppAdapter(Adapter): ...       # RunningHub OpenAPI v2
class MiniMaxAdapter(Adapter): ...     # 图像 + T2A
class MIMOAdapter(Adapter): ...        # MiMo TTS
class SeedanceAdapter(Adapter): ...    # 多供应商 + 负载均衡
```

Executor 不关心具体后端实现，只通过 `Adapter.execute()` 接口调用。

### 5.3 注册表模式（Registry Pattern）

系统中有两个全局注册表：

| 注册表 | 全局单例 | 注册时机 | 用途 |
|--------|---------|---------|------|
| `PipelineRegistry` | `pipeline_registry` | 启动时从 YAML 加载 | Pipeline / Node 定义管理 |
| `AdapterRegistry` | `backend_registry` | 启动时 `init_backends()` | Adapter 实例管理 |

### 5.4 责任链模式（Router 路由）

路由策略按优先级依次尝试，直到找到合适的 Pipeline：

```
用户指定 → 输入档案匹配 → 可用性过滤 → AI 推荐 → 优先级兜底（组内随机）
```

### 5.5 Spec-Provider 模式（Seedance）

Seedance 后端采用"供应商无关 Spec → 供应商驱动"的两层解耦：

```
GenerationRequest
    │
    ▼
classify_video_spec(request) → VideoGenSpec
    │  shape: T2V / I2V / FLF / Multimodal / Video_Edit / Video_Extend
    │  media: list[SpecMedia] (kind, role, ref, index)
    │  ordered_segments: list[OrderedSegment] (text/media 交错)
    │
    ▼
SeedanceProvider.render_create(spec, model) → (method, url, headers, body)
    │
    ▼
SeedanceProvider._request(method, url, ...) → resp_json
    │
    ▼
SeedanceProvider.parse_create(resp_json) → task_id
    │
    ▼
SeedanceProvider.poll_until_done(task_id) → NormalizedTask
    │
    ▼
SeedanceAdapter 包装为 NodeOutput
```

三个供应商（ARK / Gateway / RunningHub）共享同一 `VideoGenSpec` 协议，只在 render / parse / get 三个方法上体现差异。

---

## 六、模块职责速查表

| 模块 | 目录 | 职责 | 关键类/函数 |
|------|------|------|-----------|
| **插件入口** | `RH_ComfyUI/__init__.py` | 注册 Plugins，启动钩子，挂载 rh_models | `Plugins()`, `init_pipeline_system()` |
| **生成命令** | `RH_ComfyUI/rh_generate/` | 接收生成命令，执行生成流程（含积分退还） | `generate_image()`, `_do_generate()` |
| **积分管理** | `RH_ComfyUI/rh_admin/` | 积分增删查（命令 + AI Tools） | `add_user_points()`, `query_user_points()` |
| **AI 代理** | `RH_ComfyUI/rh_agent/` | 注册 AIGC 能力代理画像 | `register_rh_aigc_agent()` |
| **模型清单** | `RH_ComfyUI/rh_models/` | 命令 + FastAPI + AI 工具 | `build_model_catalog()`, `/RH_ComfyUI/models` |
| **帮助系统** | `RH_ComfyUI/rh_help/` | 帮助命令 + 全局注册 | `send_help()`, `register_help()` |
| **服务配置** | `RH_ComfyUI/rh_config/service_config.py` | 上游服务连接（API Key / BaseURL / Seedance） | `SERVICE_CONFIG_DEFAULT` |
| **插件配置** | `RH_ComfyUI/rh_config/plugin_config.py` | 并发上限 + 积分规则 | `PLUGIN_CONFIG_DEFAULT` |
| **核心引擎** | `RH_ComfyUI/utils/core/` | 请求模型、Pipeline、路由、执行 | `GenerationRequest`, `NodeDef`, `route()`, `execute_generation()` |
| **后端抽象** | `RH_ComfyUI/utils/backends/` | Adapter 基类 + 6 个实现 | `Adapter`, `ComfyUIAdapter`, `GPTImage2Adapter`, `SeedanceAdapter` |
| **Seedance 子层** | `RH_ComfyUI/utils/backends/seedance/` | Spec/Classify/Provider/Registry | `classify_video_spec()`, `SeedanceProvider` |
| **参数映射** | `RH_ComfyUI/utils/mappers/` | 编程式参数映射函数 | `gpt_image2_mapper`, `wan_videogen_mapper` |
| **数据库** | `RH_ComfyUI/utils/database/` | 积分表 ORM 模型 | `RHBind` |
| **资源管理** | `RH_ComfyUI/utils/resource/` | 路径常量 + 工作流加载 | `RESOURCE_PATH`, `load_workflow()` |
| **积分检查** | `RH_ComfyUI/utils/points.py` | 积分充足性检查 | `check_point()` |
| **图片预处理** | `RH_ComfyUI/utils/image_process.py` | 缩放 / EXIF / 视频前预处理 | `preprocess_for_video()` |

---

## 七、数据流向图

```
                    ┌──────────────────┐
                    │   YAML Pipeline  │
                    │   定义文件       │
                    └────────┬─────────┘
                             │ load_from_directory()
                             ▼
                    ┌──────────────────┐
                    │ PipelineRegistry │
                    │ (全局单例)       │
                    └────────┬─────────┘
                             │
    ┌────────────────────────┼─────────────────────────┐
    │                        │                         │
    ▼                        ▼                         ▼
┌────────┐           ┌────────────┐           ┌────────────────┐
│ Parser │           │   Router   │           │ rh_models Web  │
│ 解析   │           │   路由     │           │ API / 命令     │
│ (token)│           │(输入档案   │           │ build_model_   │
│        │           │ +能力)     │           │ catalog()      │
└───┬────┘           └─────┬──────┘           └────────┬───────┘
    │ model_token          │ NodeDef                   │ ModelEntry
    │ prompt               │ (含 inputs/capabilities)  │
    └──────────┬───────────┘                           │
               ▼                                       │
    ┌──────────────────┐                                │
    │ GenerationRequest│───────────────────────────────┘
    │ (统一请求)       │
    └──────────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Backend.execute()│
                    │ (Adapter 分发)   │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────────────┐
        │ ComfyUI  │  │ GPT-     │  │ SeedanceAdapter  │
        │ WebSocket│  │ Image2   │  │ → classify →     │
        │ +  workflow│ │ REST    │  │ provider.run →  │
        │ override │  │          │  │ download →        │
        └────┬─────┘  └────┬─────┘  │ NodeOutput       │
             │              │        └─────────┬─────────┘
             ▼              ▼                  ▼
        ┌──────────────────────────────────────┐
        │         NodeOutput                   │
        │   (data: bytes, outputs, usage, raw) │
        │   GenerationResult.from_node_output  │
        └──────────────────┬───────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  executor    │
                    │  _save_output│
                    │  → OUTPUT_PATH│
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  bot.send()  │
                    │  发送结果    │
                    └──────────────┘
```

---

## 八、配置管理体系

```
rh_config/
├── comfyui_config.py
│     SERVICE_CONFIG = StringConfig("RHComfyUI-Service", ...)
│     PLUGIN_CONFIG  = StringConfig("RHComfyUI-Plugin", ...)
│
├── service_config.py
│     SERVICE_CONFIG_DEFAULT = {
│         ComfyUI: ComfyUI_BaseURL, RH_apikey
│         MiniMax: MiniMax_apikey
│         MiMo:   MIMO_apikey
│         Seedance: Seedance_apikey_{ark,gateway,runninghub}
│                   Seedance_BaseURL_{ark,gateway,runninghub}
│                   Seedance_Enable_{ark,gateway,runninghub}
│                   Seedance_Load_Balance, Seedance_Failure_Threshold
│                   Seedance_Dry_Run
│         OpenAI:  OpenAI_Image_apikey, OpenAI_Image_BaseURL
│     }
│
└── plugin_config.py
      PLUGIN_CONFIG_DEFAULT = {
          Max_Concurrency,
          Default_Point,
          Draw_Point,
          Edit_Image_Point,
          Music_Point,
          Speech_Point,
          Video_Point,
      }

引用关系：
  SERVICE_CONFIG → utils/backends/comfyui/api.py (ComfyUI_BaseURL)
                 → utils/backends/comfyui/api.py (RH_apikey)
                 → utils/backends/rh_app/api.py (RH_apikey)
                 → utils/backends/gpt_image2/api.py (OpenAI_Image_apikey)
                 → utils/backends/minimax/api.py (MiniMax_apikey)
                 → utils/backends/mimo/api.py (MIMO_apikey)
                 → utils/backends/seedance/executor.py
                   (Seedance_apikey_* / Seedance_BaseURL_* / Seedance_Enable_*)

  PLUGIN_CONFIG  → utils/core/executor.py   (Max_Concurrency → Semaphore)
                 → utils/database/models.py (Default_Point → 新用户积分)
                 → utils/points.py          (历史 *_Point 常量)
                 → node.point_cost          (YAML 声明的各业务积分消耗)
```

---

## 九、扩展指南

### 添加新模型（声明式，无需改代码）

1. 在 `data/RHComfyUI/pipelines/imagegen/`（或 `videogen/music/speech/`）下创建 YAML 文件
2. 如需工作流 JSON，放在 `data/RHComfyUI/workflow/图片生成/`（或对应目录）
3. 重启插件，系统自动加载

### 添加新模型（编程式，需写映射函数）

1. 在 `utils/mappers/` 下创建映射函数
2. 在 `utils/mappers/__init__.py` 中导出
3. 在 `data/RHComfyUI/pipelines/` 下创建 YAML，指定 `mode: programmatic` 和 `mapper` 路径

### 添加新后端

1. 在 `utils/backends/` 下创建新目录
2. 实现 `Adapter` 基类的五个成员（`name` / `check_available` / `get_unavailable_reason` / `capabilities` / `execute`）
3. 在 `utils/backends/__init__.py` 的 `init_backends()` 中注册
4. 在 `utils/backends/<your_backend>/api.py` 中实现协议层 HTTP 客户端

### 添加 Seedance 新供应商

1. 在 `utils/backends/seedance/providers/` 下新建 `<vendor>.py`，实现 `SeedanceProvider` 子类
2. 在 `utils/backends/seedance/providers/__init__.py` 中导出
3. 在 `utils/backends/seedance/registry.py` 的 `_PROVIDERS` 字典中注册
4. 在 `rh_config/service_config.py` 的 `SERVICE_CONFIG_DEFAULT` 中添加对应的 `Seedance_apikey_<vendor>` / `Seedance_BaseURL_<vendor>` / `Seedance_Enable_<vendor>` 三项配置

---

## 迁移对照表（旧架构 → 新架构）

> 本节列出 **本次代码迭代中** 与旧文档不符的关键变更，便于阅读旧资料时建立映射。

| 维度 | 旧版（已废弃） | 新版（现行） |
|------|--------------|-------------|
| 任务类型 | 7 种 TaskType（text2image/image2image/image_edit/text2video/image2video/music/speech） | 4 种 TaskType（IMAGE/VIDEO/MUSIC/SPEECH），旧值由 `normalize_task_type()` 在加载时归一化 |
| Pipeline 数据类 | `PipelineDef` | `NodeDef`（`PipelineDef` 作为别名） |
| Pipeline 字段 | name/display_name/task_type/backend/point_cost/mode/mappings | + `backend_model` / `backend_models` / `provider` / `inputs` / `outputs` / `capabilities` |
| 后端基类 | `Backend`（含 `check_available` / `get_unavailable_reason` / `execute`） | `Adapter`（+ `capabilities` / `execute` 返回 `NodeOutput` / `on_progress` 回调）；`Backend = Adapter` |
| 后端实现 | ComfyUI / GPT-Image2 / RH App / MiniMax / MiMo（5 个） | + **Seedance**（多供应商：ARK / Gateway / RunningHub） |
| GPT-Image2 标识 | `gpt_image2` | `gpt-image-2`（注意连字符） |
| 节点输出 | `GenerationResult` | `NodeOutput`（更丰富，含 outputs/usage/raw；`GenerationResult.from_node_output()` 兼容） |
| 端口类型 | 无显式声明 | `PortSpec` / `PortType`（驱动 Router 输入档案匹配） |
| 多模态内容 | 无 | `MediaRef` / `ContentItem` / `ordered_content`（Seedance 2.0 风格） |
| 进度上报 | 无 | `ProgressEvent` + `on_progress` 回调 |
| 能力声明 | `PRIORITY` 字典（硬编码） | `CapabilityManifest`（每个 Adapter/Node 自报家门，Router 不硬编码） |
| 路由 | 按细分任务类型圈桶 | 按 4 种输出模态圈桶 + 输入档案过滤 |
| 配置 | `RHCOMFYUI_CONFIG` 单实例 | `SERVICE_CONFIG` + `PLUGIN_CONFIG` 双实例（按 `GsDivider` 分组） |
| 配置项命名 | `ComfyUI_BaseURL` / `RH_apikey` / `GPT_Image2_apikey` / `GPT_Image2_BaseURL` / `MiniMax_apikey` / `MIMO_apikey` | + `OpenAI_Image_apikey` / `OpenAI_Image_BaseURL`（GPT-Image2 的新名，旧名保留兼容）；+ Seedance 多供应商配置 |
| 积分检查 | `_do_generate()` 用 `points.py` 的模块常量 | `_do_generate()` 用 `node.point_cost`（YAML 声明） |
| 失败处理 | 仅记录日志 | 自动退还积分 + 错误消息返回 AI |
| Pipeline YAML 路径 | `text2image/` + `image2image/` + `image_edit/` | `imagegen/`（统一） |
| Pipeline YAML 路径 | `text2video/` + `image2video/` | `videogen/`（统一） |
| 工作流覆盖 | 无 | `ComfyUIAPI.set_workflow_override()` — mapper 可声明本次使用的工作流（如 Wan 2.2 的 t2v/i2v 切换） |
| 节点分发 | 通过 `utils/ai_tools.py` | 通过 `rh_admin/commands.py` 的 `@ai_tools` + `rh_agent/__init__.py` 的能力代理画像；旧 `utils/ai_tools.py` 不再存在 |
| 模型清单 | 仅 `rh_generate.list_models` | + `rh_models` 模块（命令 `模型列表` + FastAPI `/RH_ComfyUI/models` + AI 工具 `ai_list_models`） |
| Seedance | 不存在 | 独立子模块，含 Spec/Classify/Provider/Registry/Debug，支持负载均衡 + 熔断 + Dry-Run |
| RunningHub ComfyUI | 总是 WebSocket | 自动识别 `runninghub` 字串后切换为 `/history` 轮询 + `/openapi/v2/query` 兜底 |
| 核心类型系统 | 只有 `GenerationRequest` / `GenerationResult` | + `types.py`：`PortSpec` / `PortType` / `MediaKind` / `MediaRef` / `ContentItem` / `ContentItemType` / `CapabilityManifest` / `ProgressEvent` / `NodeOutput` |