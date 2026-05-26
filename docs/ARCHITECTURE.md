# RH_ComfyUI 项目架构文档

## 目录

- [RH\_ComfyUI 项目架构文档](#rh_comfyui-项目架构文档)
  - [目录](#目录)
  - [一、项目概述](#一项目概述)
  - [二、整体架构图](#二整体架构图)
  - [三、分层架构详解](#三分层架构详解)
    - [3.1 命令层（Command Layer）](#31-命令层command-layer)
      - [`rh_generate` — 生成命令模块](#rh_generate--生成命令模块)
      - [`rh_admin` — 积分管理模块](#rh_admin--积分管理模块)
      - [`rh_agent` — AI 代理注册模块](#rh_agent--ai-代理注册模块)
      - [`rh_help` — 帮助模块](#rh_help--帮助模块)
    - [3.2 核心引擎层（Core Engine Layer）](#32-核心引擎层core-engine-layer)
      - [`request.py` — 统一请求/响应模型](#requestpy--统一请求响应模型)
      - [`pipeline.py` — Pipeline 注册表](#pipelinepy--pipeline-注册表)
      - [`router.py` — 智能路由器](#routerpy--智能路由器)
      - [`executor.py` — 统一执行器](#executorpy--统一执行器)
      - [`parser.py` — 命令解析器](#parserpy--命令解析器)
    - [3.3 后端抽象层（Backend Abstraction Layer）](#33-后端抽象层backend-abstraction-layer)
      - [`base.py` — Backend 抽象基类](#basepy--backend-抽象基类)
      - [三个后端实现](#三个后端实现)
      - [`mappers/` — 参数映射函数](#mappers--参数映射函数)
    - [3.4 资源层（Resource Layer）](#34-资源层resource-layer)
      - [`resource/RESOURCE_PATH.py`](#resourceresource_pathpy)
      - [Pipeline YAML 定义](#pipeline-yaml-定义)
    - [3.5 基础设施层（Infrastructure Layer)](#35-基础设施层infrastructure-layer)
      - [`rh_config` — 配置管理](#rh_config--配置管理)
      - [`database/models.py` — 数据库](#databasemodelspy--数据库)
      - [`points.py` — 积分检查](#pointspy--积分检查)
  - [四、消息流转机制](#四消息流转机制)
    - [4.1 完整消息流转图](#41-完整消息流转图)
    - [4.2 以「生图」为例的详细流程](#42-以生图为例的详细流程)
    - [4.3 AI Agent 调用流程](#43-ai-agent-调用流程)
  - [五、核心设计模式](#五核心设计模式)
    - [5.1 声明式 Pipeline 架构](#51-声明式-pipeline-架构)
    - [5.2 策略模式（Backend 抽象）](#52-策略模式backend-抽象)
    - [5.3 注册表模式（Registry Pattern）](#53-注册表模式registry-pattern)
    - [5.4 责任链模式（Router 路由）](#54-责任链模式router-路由)
  - [六、模块职责速查表](#六模块职责速查表)
  - [七、数据流向图](#七数据流向图)
  - [八、配置管理体系](#八配置管理体系)
  - [九、扩展指南](#九扩展指南)
    - [添加新模型（声明式，无需改代码）](#添加新模型声明式无需改代码)
    - [添加新模型（编程式，需写映射函数）](#添加新模型编程式需写映射函数)
    - [添加新后端](#添加新后端)

---

## 一、项目概述

RH_ComfyUI 是一个基于 GsCore 机器人框架的 **AIGC 统一生成插件**（v2.0.0），支持通过聊天机器人命令或 AI Agent 自动调用来完成图片生成、图片编辑、视频生成、音乐创作和语音合成等多种 AIGC 任务。

**核心设计理念：**
- **多后端统一抽象**：通过 Backend 基类统一 ComfyUI、BLT、RunningHub 三种后端
- **声明式 Pipeline 架构**：通过 YAML 文件定义工作流，无需修改代码即可添加新模型
- **智能路由**：自动选择最合适的模型，支持用户指定、AI 推荐、优先级兜底
- **积分经济系统**：通过积分控制生成成本，支持管理员管理

**技术栈：**
- Python 3.10+
- GsCore 插件框架
- SQLModel / SQLAlchemy（数据库）
- httpx / aiohttp / websockets（HTTP/WS 通信）
- PIL / Pillow（图片处理）
- PyYAML（配置解析）

---

## 二、整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户 / AI Agent                              │
│                  "rh 生图 qwen 一只可爱的猫咪"                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     命令层 (Command Layer)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  rh_generate  │  │   rh_admin   │  │  rh_agent / rh_help     │  │
│  │  生图/改图/   │  │  积分管理     │  │  AI代理注册 / 帮助系统   │  │
│  │  生视频/音乐  │  │              │  │                          │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────────┘  │
└─────────┼──────────────────┼────────────────────────────────────────┘
          │                  │
          ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    核心引擎层 (Core Engine)                          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────┐  │
│  │   Parser    │ │   Router   │ │  Executor  │ │ PipelineReg.   │  │
│  │ 命令解析器  │ │ 智能路由器  │ │ 统一执行器 │ │ Pipeline注册表 │  │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └───────┬────────┘  │
│        │              │              │                 │           │
│  ┌─────┴──────────────┴──────────────┴─────────────────┴────────┐  │
│  │              request.py (GenerationRequest/Result)            │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   后端抽象层 (Backend Layer)                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Backend (ABC)                              │  │
│  │  check_available() / execute() / get_unavailable_reason()    │  │
│  └────────┬─────────────────┬──────────────────┬───────────────┘  │
│           │                 │                  │                   │
│  ┌────────▼────────┐ ┌─────▼──────────┐ ┌─────▼──────────────┐  │
│  │  ComfyUIBackend │ │  BLTBackend    │ │  RHAppBackend      │  │
│  │  (WebSocket+    │ │  (OpenAI 兼容  │ │  (RunningHub       │  │
│  │   HTTP API)     │ │   REST API)    │ │   OpenAPI v2)      │  │
│  └────────┬────────┘ └─────┬──────────┘ └─────┬──────────────┘  │
│           │                 │                  │                   │
│  ┌────────▼────────┐ ┌─────▼──────────┐ ┌─────▼──────────────┐  │
│  │  ComfyUIAPI     │ │  BLTAPI        │ │  RHAppAPI          │  │
│  │  WebSocket客户端│ │  HTTP客户端    │ │  HTTP客户端        │  │
│  └─────────────────┘ └────────────────┘ └────────────────────┘  │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                   Mappers (参数映射)                          │  │
│  │  blt_text2image / image_edit / video / music / speech ...    │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   资源层 (Resource Layer)                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │  Pipeline YAML   │  │  Workflow JSON   │  │  RESOURCE_PATH  │  │
│  │  (模型定义)      │  │  (ComfyUI工作流) │  │  (路径管理)     │  │
│  └──────────────────┘  └──────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   基础设施层 (Infrastructure)                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │  rh_config       │  │  database/models │  │  points.py      │  │
│  │  (StringConfig)  │  │  (RHBind积分表)  │  │  (积分检查)     │  │
│  └──────────────────┘  └──────────────────┘  └─────────────────┘  │
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

**内部通用执行函数 [`_do_generate()`](RH_ComfyUI/rh_generate/__init__.py:55)：**

```python
async def _do_generate(request: GenerationRequest, ev: Event, bot: Bot) -> Optional[GenerationResult]:
    # 1. 智能路由
    pipeline = await route(request)
    # 2. 积分检查
    success, msg = await check_point(ev, pipeline.point_cost)
    # 3. 执行生成
    result = await execute_generation(request, pipeline)
    return result
```

**`to_ai=` 机制：** 每个命令都通过 `to_ai=` 参数注册为 AI 工具，AI Agent 可直接调用。

#### [`rh_admin`](RH_ComfyUI/rh_admin/__init__.py:1) — 积分管理模块

**职责：** 提供积分的增删查功能，同时支持命令和 AI Tools。

**核心函数（同时注册为 `@ai_tools`）：**
- [`add_user_points()`](RH_ComfyUI/rh_admin/commands.py:107) — 增加积分（需管理员权限）
- [`deduct_user_points()`](RH_ComfyUI/rh_admin/commands.py:141) — 扣除积分（需管理员权限）
- [`query_user_points()`](RH_ComfyUI/rh_admin/commands.py:184) — 查询积分

#### [`rh_agent`](RH_ComfyUI/rh_agent/__init__.py:1) — AI 代理注册模块

**职责：** 注册 AIGC 创作能力代理画像，让 AI Agent Mesh 自动路由 AIGC 任务。

#### [`rh_help`](RH_ComfyUI/rh_help/__init__.py:1) — 帮助模块

**职责：** 提供帮助信息，注册到全局帮助一览。

### 3.2 核心引擎层（Core Engine Layer）

核心引擎层是整个系统的**心脏**，实现了「声明式 Pipeline 架构」的核心抽象。

#### [`request.py`](RH_ComfyUI/utils/core/request.py:1) — 统一请求/响应模型

定义了覆盖所有 AIGC 任务的统一数据模型：

- [`TaskType`](RH_ComfyUI/utils/core/request.py:10) — 7种任务类型枚举
- [`OutputType`](RH_ComfyUI/utils/core/request.py:22) — 3种输出类型枚举
- [`GenerationRequest`](RH_ComfyUI/utils/core/request.py:64) — 统一请求模型
- [`GenerationResult`](RH_ComfyUI/utils/core/request.py:123) — 统一响应模型

#### [`pipeline.py`](RH_ComfyUI/utils/core/pipeline.py:1) — Pipeline 注册表

- [`PipelineDef`](RH_ComfyUI/utils/core/pipeline.py:17) — Pipeline 定义数据类
- [`PipelineRegistry`](RH_ComfyUI/utils/core/pipeline.py:41) — 注册表（从 YAML 自动加载）
- [`pipeline_registry`](RH_ComfyUI/utils/core/pipeline.py:132) — 全局单例

#### [`router.py`](RH_ComfyUI/utils/core/router.py:1) — 智能路由器

[`route(request)`](RH_ComfyUI/utils/core/router.py:35) — 四级路由策略：

1. **用户显式指定** → 精确/模糊匹配
2. **可用性过滤** → 检查后端 `check_available()`
3. **AI 推荐** → 临时 AI Agent 从可用模型中选择
4. **优先级兜底** → 按 `PRIORITY` 配置选择

#### [`executor.py`](RH_ComfyUI/utils/core/executor.py:1) — 统一执行器

[`execute_generation(request, pipeline)`](RH_ComfyUI/utils/core/executor.py:30) — 唯一执行路径，受全局 Semaphore 限流。

#### [`parser.py`](RH_ComfyUI/utils/core/parser.py:1) — 命令解析器

[`parse_model_from_prompt(text, task_type)`](RH_ComfyUI/utils/core/parser.py:31) — 从用户输入提取模型名和 prompt。

### 3.3 后端抽象层（Backend Abstraction Layer）

#### [`base.py`](RH_ComfyUI/utils/backends/base.py:1) — Backend 抽象基类

所有后端必须实现三个方法：`check_available()`、`get_unavailable_reason()`、`execute()`。

#### 三个后端实现

| 后端 | 文件 | 标识 | 连接方式 | 特点 |
|------|------|------|---------|------|
| ComfyUI | [`comfyui/`](RH_ComfyUI/utils/backends/comfyui/) | `comfyui` | WebSocket + HTTP | 支持工作流 JSON，支持声明式/编程式映射 |
| BLT | [`blt/`](RH_ComfyUI/utils/backends/blt/) | `blt` | HTTP REST | OpenAI 兼容 API，仅支持编程式映射 |
| RH App | [`rh_app/`](RH_ComfyUI/utils/backends/rh_app/) | `rh_app` | HTTP REST | RunningHub 原生应用，nodeInfoList 映射 |

#### [`mappers/`](RH_ComfyUI/utils/mappers/) — 参数映射函数

将 `GenerationRequest` 转换为各后端所需的参数格式。10个映射函数覆盖所有任务类型。

### 3.4 资源层（Resource Layer）

#### [`resource/RESOURCE_PATH.py`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:1)

路径管理 + 工作流加载 + 目录初始化。模块导入时自动将内置资源复制到运行时目录。

#### Pipeline YAML 定义

12个内置 YAML 文件定义了所有可用的 Pipeline（模型），支持用户在运行时目录扩展。

### 3.5 基础设施层（Infrastructure Layer)

#### [`rh_config`](RH_ComfyUI/rh_config/comfyui_config.py:1) — 配置管理

`RHCOMFYUI_CONFIG` 全局单例，管理 11 个配置项（后端连接、积分消耗、并发限制等）。

#### [`database/models.py`](RH_ComfyUI/utils/database/models.py:1) — 数据库

`RHBind` 积分表，提供积分的增删改查，注册到 Web 控制台。

#### [`points.py`](RH_ComfyUI/utils/points.py:1) — 积分检查

`check_point()` 函数，检查积分是否充足并自动扣除。

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
│  parse_model_from_prompt("qwen 一只可爱的猫咪")  │
│  → model = "qwen_2512"                          │
│  → prompt = "一只可爱的猫咪"                      │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ② 构建 GenerationRequest                         │
│  GenerationRequest(                              │
│    task_type = TEXT2IMAGE,                        │
│    prompt = "一只可爱的猫咪",                     │
│    model = "qwen_2512",                          │
│    width = 720, height = 1280                    │
│  )                                               │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ③ Router 智能路由                                │
│  route(request)                                  │
│  → Step 1: 用户指定 "qwen_2512" → 精确匹配成功   │
│  → 检查 ComfyUI 后端可用 → ✅                    │
│  → 返回 PipelineDef(name="qwen_2512")           │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ④ 积分检查                                       │
│  check_point(ev, pipeline.point_cost=2)          │
│  → RHBind.deduct_point() → ✅ 扣除2积分          │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑤ Executor 统一执行                              │
│  execute_generation(request, pipeline)            │
│  → 获取 Semaphore（并发限制）                     │
│  → backend = backend_registry.get("comfyui")     │
│  → backend.execute(request, pipeline)             │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│ ⑥ ComfyUIBackend.execute()                       │
│  → 加载 workflow JSON (qwen_2512.json)           │
│  → 声明式映射: prompt→node108, width→node107     │
│  → api.generate_image_by_prompt(workflow)         │
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
│  bot.send(convert_img(result.data))              │
└─────────────────────────────────────────────────┘
```

### 4.2 以「生图」为例的详细流程

**代码路径：**

```
rh_generate/__init__.py:generate_image()
  ├── core/parser.py:parse_model_from_prompt()     # 解析模型名
  ├── core/request.py:GenerationRequest()           # 构建请求
  ├── rh_generate/__init__.py:_do_generate()        # 通用执行流程
  │     ├── core/router.py:route()                  # 智能路由
  │     ├── utils/points.py:check_point()           # 积分检查
  │     │     └── database/models.py:RHBind.deduct_point()
  │     └── core/executor.py:execute_generation()   # 统一执行
  │           └── backends/comfyui/executor.py:ComfyUIBackend.execute()
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

**核心思想：** 将模型的元数据、参数映射规则从代码中抽离到 YAML 文件，实现「零代码添加新模型」。

```yaml
# 只需一个 YAML 文件即可添加新模型
name: "my_new_model"
display_name: "我的新模型"
task_type: text2image
backend: comfyui
point_cost: 3
workflow: "my_workflow.json"
mode: declarative
mappings:
  - source: prompt
    target: "1.inputs.text"
  - source: width
    target: "2.inputs.width"
    default: 1024
```

**两种映射模式：**
- **声明式**（`declarative`）：YAML 中定义 `source → target` 映射规则
- **编程式**（`programmatic`）：Python 函数处理复杂逻辑（如图片上传、条件判断）

### 5.2 策略模式（Backend 抽象）

```python
class Backend(ABC):
    @abstractmethod
    async def execute(self, request, pipeline) -> GenerationResult: ...

class ComfyUIBackend(Backend): ...  # WebSocket + 工作流 JSON
class BLTBackend(Backend): ...      # OpenAI 兼容 REST API
class RHAppBackend(Backend): ...    # RunningHub 原生应用
```

Executor 不关心具体后端实现，只通过 `Backend.execute()` 接口调用。

### 5.3 注册表模式（Registry Pattern）

系统中有三个全局注册表：

| 注册表 | 全局单例 | 注册时机 | 用途 |
|--------|---------|---------|------|
| `PipelineRegistry` | `pipeline_registry` | 启动时从 YAML 加载 | Pipeline 定义管理 |
| `BackendRegistry` | `backend_registry` | 启动时 `init_backends()` | 后端实例管理 |

### 5.4 责任链模式（Router 路由）

路由策略按优先级依次尝试，直到找到合适的 Pipeline：

```
用户指定 → 可用性过滤 → AI 推荐 → 优先级兜底 → 随机选择
```

---

## 六、模块职责速查表

| 模块 | 目录 | 职责 | 关键类/函数 |
|------|------|------|-----------|
| **插件入口** | `RH_ComfyUI/__init__.py` | 注册 Plugins，启动钩子 | `Plugins()`, `init_pipeline_system()` |
| **生成命令** | `RH_ComfyUI/rh_generate/` | 接收生成命令，执行生成流程 | `generate_image()`, `_do_generate()` |
| **积分管理** | `RH_ComfyUI/rh_admin/` | 积分增删查 | `add_user_points()`, `query_user_points()` |
| **AI 代理** | `RH_ComfyUI/rh_agent/` | 注册 AIGC 能力代理画像 | `register_rh_aigc_agent()` |
| **帮助系统** | `RH_ComfyUI/rh_help/` | 帮助命令 + 全局注册 | `send_help()`, `register_help()` |
| **配置管理** | `RH_ComfyUI/rh_config/` | 插件配置定义与读取 | `RHCOMFYUI_CONFIG`, `CONFIG_DEFAULT` |
| **核心引擎** | `RH_ComfyUI/utils/core/` | 请求模型、Pipeline、路由、执行 | `GenerationRequest`, `route()`, `execute_generation()` |
| **后端抽象** | `RH_ComfyUI/utils/backends/` | Backend 基类 + 三个实现 | `Backend`, `ComfyUIBackend`, `BLTBackend`, `RHAppBackend` |
| **参数映射** | `RH_ComfyUI/utils/mappers/` | 编程式参数映射函数 | `banana2_mapper()`, `qwen_edit_mapper()` |
| **数据库** | `RH_ComfyUI/utils/database/` | 积分表 ORM 模型 | `RHBind` |
| **资源管理** | `RH_ComfyUI/utils/resource/` | 路径常量 + 工作流加载 | `RESOURCE_PATH`, `load_workflow()` |
| **积分检查** | `RH_ComfyUI/utils/points.py` | 积分充足性检查 | `check_point()` |

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
    ┌────────────────────────┼────────────────────────┐
    │                        │                        │
    ▼                        ▼                        ▼
┌────────┐           ┌────────────┐           ┌────────────┐
│ Parser │           │   Router   │           │  Executor  │
│ 解析   │           │   路由     │           │   执行     │
└───┬────┘           └─────┬──────┘           └─────┬──────┘
    │                      │                        │
    │ model_name           │ PipelineDef            │
    │ prompt               │                        │
    └──────────┬───────────┘                        │
               │                                    │
               ▼                                    │
    ┌──────────────────┐                            │
    │ GenerationRequest│────────────────────────────┘
    │ (统一请求)       │
    └──────────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │ Backend.execute()│
                    │ (后端分发)       │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ ComfyUI  │  │   BLT    │  │  RH App  │
        │ WebSocket│  │  REST    │  │  REST    │
        └────┬─────┘  └────┬─────┘  └────┬─────┘
             │              │              │
             ▼              ▼              ▼
        ┌──────────────────────────────────────┐
        │         GenerationResult             │
        │   (data: bytes, output_type, ...)    │
        └──────────────────┬───────────────────┘
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
rh_config/config_default.py
    │ CONFIG_DEFAULT: Dict[str, GSC]
    │
    ▼
rh_config/comfyui_config.py
    │ RHCOMFYUI_CONFIG = StringConfig(...)
    │
    ├──→ Max_Concurrency ──→ utils/core/executor.py (Semaphore)
    ├──→ ComfyUI_BaseURL ──→ utils/backends/comfyui/api.py (连接地址)
    ├──→ RH_apikey ────────→ utils/backends/comfyui/api.py + rh_app/api.py
    ├──→ BLT_apikey ───────→ utils/backends/blt/api.py
    ├──→ BLT_API_URL ──────→ utils/backends/blt/api.py
    ├──→ Default_Point ────→ utils/database/models.py (新用户积分)
    └──→ *_Point ──────────→ utils/points.py (各任务积分消耗)
```

---

## 九、扩展指南

### 添加新模型（声明式，无需改代码）

1. 在 `data/RHComfyUI/pipelines/` 下创建 YAML 文件
2. 如需工作流 JSON，放在 `data/RHComfyUI/workflow/` 对应子目录
3. 重启插件，系统自动加载

### 添加新模型（编程式，需写映射函数）

1. 在 `utils/mappers/` 下创建映射函数
2. 在 `utils/mappers/__init__.py` 中导出
3. 在 `data/RHComfyUI/pipelines/` 下创建 YAML，指定 `mode: programmatic` 和 `mapper` 路径

### 添加新后端

1. 在 `utils/backends/` 下创建新目录
2. 实现 `Backend` 基类的三个方法
3. 在 `utils/backends/__init__.py` 的 `init_backends()` 中注册
