# RH_ComfyUI 重构技术总结（v2.x）

> **版本**：v2.x（在 v2.0 基础上持续演进）
>
> **最后更新**：2026-06
>
> **重构范围**：在 v2.0「声明式 Pipeline + Adapter 后端抽象」基础上,新增 Seedance 多供应商、rh_models Web API、能力代理画像、任务模态收编等。当前代码已与初版重构总结有较大差异。

---

## 一、本次演进相对 v2.0 的核心变更

| # | 变更点 | 旧（v2.0 初版） | 新（现行 v2.x） | 影响文件 |
|---|--------|---------------|---------------|----------|
| C1 | 任务类型细分收编 | 7 种 TaskType（text2image/image2image/image_edit/text2video/image2video/music/speech） | **4 种** TaskType（IMAGE/VIDEO/MUSIC/SPEECH）+ 输入档案过滤；旧值由 `normalize_task_type()` 归一化 | `utils/core/request.py`、`utils/core/pipeline.py` |
| C2 | Pipeline 数据类 | `PipelineDef` | `NodeDef`（`PipelineDef` 作为别名） | `utils/core/pipeline.py` |
| C3 | 后端基类 | `Backend`（3 个方法） | `Adapter`（**5 个**成员，含 `capabilities()`；`execute` 返回 `NodeOutput`，支持 `on_progress`） | `utils/backends/base.py` |
| C4 | 后端实现 | 5 个（ComfyUI / GPT-Image2 / RH App / MiniMax / MiMo） | **6 个**：+ **Seedance**（多供应商 ARK / Gateway / RunningHub + 负载均衡 + 熔断 + Dry-Run） | `utils/backends/seedance/`（新） |
| C5 | GPT-Image2 标识 | `gpt_image2` | `gpt-image-2`（**连字符**） | `utils/backends/gpt_image2/executor.py` |
| C6 | 节点输出 | `GenerationResult` | `NodeOutput`（含 `outputs` / `usage` / `raw`；`GenerationResult.from_node_output()` 兼容） | `utils/core/types.py` |
| C7 | 类型系统 | 仅 `GenerationRequest` / `GenerationResult` | + `utils/core/types.py`：`PortSpec` / `PortType` / `MediaKind` / `MediaRef` / `ContentItem` / `ContentItemType` / `CapabilityManifest` / `ProgressEvent` / `NodeOutput` | `utils/core/types.py`（新） |
| C8 | 配置拆分 | `RHCOMFYUI_CONFIG` 单实例 | `SERVICE_CONFIG` + `PLUGIN_CONFIG` 双实例（按 `GsDivider` 分组） | `rh_config/comfyui_config.py` + `service_config.py`（新）+ `plugin_config.py`（新） |
| C9 | 配置项命名 | `GPT_Image2_apikey` / `GPT_Image2_BaseURL` | + 新名 `OpenAI_Image_apikey` / `OpenAI_Image_BaseURL`（旧名保留兼容） | `rh_config/service_config.py` |
| C10 | 积分检查 | `_do_generate()` 用 `points.py` 的模块常量 | `_do_generate()` 用 `node.point_cost`（YAML 声明） | `rh_generate/__init__.py`、`utils/points.py` |
| C11 | 失败处理 | 仅记录日志 | 自动退还积分（`RHBind.add_point()`）+ AI 友好错误信息 | `rh_generate/__init__.py` |
| C12 | Pipeline YAML 路径 | `text2image/` + `image2image/` + `image_edit/` | `imagegen/`（统一） | `utils/resource/pipelines/` |
| C13 | Pipeline YAML 路径 | `text2video/` + `image2video/` | `videogen/`（统一） | `utils/resource/pipelines/` |
| C14 | 工作流覆盖 | 无 | `ComfyUIAPI.set_workflow_override()` — mapper 可声明本次使用的工作流（如 Wan 2.2 的 t2v/i2v 切换） | `utils/backends/comfyui/api.py`、`utils/mappers/video.py` |
| C15 | AI Tools | 通过 `utils/ai_tools.py` | 通过 `rh_admin/commands.py` 的 `@ai_tools` + `rh_agent/__init__.py` 的能力代理画像 | `rh_admin/commands.py`、`rh_agent/__init__.py` |
| C16 | AI 知识库 | `register_pipeline_knowledge()` 注入 `to_ai=` | 改用 `ai_entity(KnowledgePoint(...))` 单独存储 | `rh_generate/_knowledge.py` |
| C17 | 模型清单 | 仅 `rh_generate.list_models` | + **`rh_models` 模块**（命令 `模型列表` + **FastAPI `/RH_ComfyUI/models`** + AI 工具 `ai_list_models`） | `rh_models/`（新目录） |
| C18 | Seedance | 不存在 | 独立子模块，含 Spec/Classify/Provider/Registry/Debug | `utils/backends/seedance/`（新） |
| C19 | RunningHub ComfyUI | 总是 WebSocket | 自动识别 `runninghub` 字串后切换为 `/history` 轮询 + `/openapi/v2/query` 兜底 | `utils/backends/comfyui/api.py` |
| C20 | 自动产物落盘 | 无 | `executor._save_output()` 写入 `OUTPUT_PATH/<task_type>/<ts>.<ext>` | `utils/core/executor.py` |
| C21 | 懒加载兜底 | 仅 router 有 | router + parser + rh_models.api 都有 `_ensure_runtime_initialized()` / `_ensure_registry_loaded()` 兜底 | `utils/core/router.py`、`utils/core/parser.py`、`rh_models/api.py` |
| C22 | 图片预处理 | 无独立模块 | `utils/image_process.py`：缩放 / EXIF / 视频前预处理 | `utils/image_process.py`（新） |
| C23 | 多模态附件 | 仅 `ev.image_id` | + `ev.image_id_list` / `ev.audio_id` / `ev.audio_id_list` / `ev.file` | `rh_generate/__init__.py` |

---

## 二、v2.0 初版重构的回顾（仍然有效）

> 本节回顾 v2.0 重构的核心动机与设计决策,作为 v2.x 演进的起点。仍适用于新代码,可放心引用。

### 2.1 重构动机

**旧架构的核心问题：**

| # | 问题 | 严重程度 | 旧代码位置 |
|---|------|---------|-----------|
| P1 | **命令爆炸** — 每种任务类型一个命令 | 🔴 | `rh_draw/` + `rh_video/` + `rh_audio/` |
| P2 | **无法指定模型** — 用户说"生图"后无法选择 qwen/banana | 🔴 | `wrapper.py` → `select_available_model()` |
| P3 | **工作流硬编码** — 新增工作流必须写 Python 函数 | 🔴 | `utils/comfyui/_request.py` 硬编码节点 ID |
| P4 | **后端耦合** — ComfyUI/BLT/RH 调用逻辑散落各处 | 🟡 | `utils/blt/` + `utils/comfyui/` + `utils/RH/` |
| P5 | **参数模型不统一** — 每个函数签名不同 | 🟡 | `wrapper.py` 各函数参数各异 |
| P6 | **AI 集成不完整** — 命令触发器无 `to_ai=` | 🟡 | `rh_draw/__init__.py` 等 |
| P7 | **图片输入处理不统一** — 命令层和 AI 层两套逻辑 | 🟡 | `ev.image_id` vs `RM.get(image_id)` |

### 2.2 旧架构调用链

```
用户命令 → rh_draw/rh_video/rh_audio → wrapper.py → MODEL_REGISTRY(硬编码) → _request.py(硬编码节点)
AI工具   → wrapper.py @ai_tools       → 同上
```

**核心瓶颈**：所有路径汇聚到硬编码函数,新增工作流 = 新增 Python 函数 + 修改注册表 + 修改优先级。

### 2.3 三层解耦

```
┌─────────────────────────────────────────────────────┐
│              命令 / AI 入口层                          │
│   on_command + to_ai=  |  @ai_tools                   │
│   (用户意图 → GenerationRequest)                      │
└─────────────────────┬───────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────┐
│              路由层 (Router)                          │
│   GenerationRequest → Pipeline 选择                   │
│   (可用性过滤 → AI推荐/优先级 → Pipeline)              │
└─────────────────────┬───────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────┐
│          执行层 (Pipeline + Backend)                  │
│   Pipeline: 参数映射 (声明式YAML + 可选Python映射器)  │
│   Backend:  实际执行 (ComfyUI / BLT / RH / ...)      │
└─────────────────────────────────────────────────────┘
```

> ⚠ **v2.x 变化**：v2.0 仍依赖 7 种 TaskType + 后端列表硬编码；v2.x 改为 4 种 TaskType + 输入档案过滤 + Adapter 自报家门（`capabilities()`），路由层不再硬编码任何后端列表。

### 2.4 核心原则（仍然有效）

| 原则 | 说明 | v2.x 落实情况 |
|------|------|-------------|
| **任务驱动，而非模型驱动** | 用户说"我要生图"，不说"我要用qwen生图" | ✅（按 4 种 TaskType 路由） |
| **声明式优先** | 新增工作流 = 新增 YAML + JSON，零 Python 改动 | ✅ |
| **Backend 可插拔** | 所有后端都是等价 Adapter，统一接口 | ✅（6 个 Adapter 实现） |
| **双入口对等** | 命令触发器和 AI 工具访问同一套逻辑 | ✅（rh_generate 入口 + rh_agent 画像） |
| **参数统一** | 所有任务类型共用 `GenerationRequest` | ✅ |

---

## 三、当前新增文件清单（v2.x 演进）

### 3.1 核心层 `utils/core/`

| 文件 | 职责 |
|------|------|
| `request.py` | `GenerationRequest` / `GenerationResult` / `TaskType` / `OutputType` 统一参数模型（30+ 字段；含 `normalize_task_type()` 旧值归一化） |
| `pipeline.py` | `NodeDef` / `PipelineRegistry` — 从 YAML 自动加载节点定义（含 `inputs` / `outputs` / `capabilities` 自动推断；`PipelineDef` 作为向后兼容别名） |
| `router.py` | `route()` 智能路由 — 用户指定→输入档案匹配→可用性过滤→AI推荐→优先级兜底 |
| `executor.py` | `execute_generation()` 统一执行入口（含全局 Semaphore + 自动产物落盘） |
| `parser.py` | `parse_model_from_prompt()` 从命令文本中提取可选模型名（只返回 token，不解析为 NodeDef）+ `parse_mood_from_prompt()` 情绪标签 |
| `types.py`（**新**） | `PortSpec` / `PortType` / `MediaKind` / `MediaRef` / `ContentItem` / `ContentItemType` / `CapabilityManifest` / `ProgressEvent` / `NodeOutput` |

### 3.2 后端层 `utils/backends/`

| 文件 | 职责 |
|------|------|
| `base.py` | `Adapter` ABC — `name` / `check_available()` / `get_unavailable_reason()` / `capabilities()` / `execute()`（**5 个成员**） |
| `__init__.py` | `AdapterRegistry` 注册表 + `init_backends()` 注册 6 个 Adapter |
| `comfyui/api.py` | `ComfyUIAPI` — WS 连接、上传、生成（含 `set_workflow_override` 工作流覆盖 + RunningHub 代理自动切换） |
| `comfyui/executor.py` | `ComfyUIAdapter` — 声明式/编程式映射 + 工作流执行 |
| `gpt_image2/api.py` | `GPTImage2API` — OpenAI 兼容 API 客户端（兼容 OpenAI 官方 / OneAPI / NewAPI / OpenRouter / BLT / SiliconFlow / Ollama 等） |
| `gpt_image2/executor.py` | `GPTImage2Adapter`（标识 `gpt-image-2`） |
| `rh_app/api.py` | `RHAppAPI` — RunningHub OpenAPI v2 客户端 |
| `rh_app/executor.py` | `RHAppAdapter` |
| `minimax/api.py` | `MiniMaxAPI` — 图像 + T2A 异步语音 + 音色克隆 |
| `minimax/executor.py` | `MiniMaxAdapter` |
| `mimo/api.py` | `MIMOAPI` — XiaoMi MiMo TTS |
| `mimo/executor.py` | `MIMOAdapter` |
| `seedance/executor.py`（**新**） | `SeedanceAdapter` — 多供应商负载均衡 + 熔断 + Dry-Run |
| `seedance/spec.py`（**新**） | `VideoTaskShape` / `MediaRole` / `VideoGenSpec` 等供应商无关的数据类 |
| `seedance/classify.py`（**新**） | `classify_video_spec()` 按输入自动判定任务形态 |
| `seedance/provider.py`（**新**） | `SeedanceProvider` ABC + `NormalizedTask` + `SeedanceProviderError` + `normalize_usage()` |
| `seedance/registry.py`（**新**） | 供应商选择 + 负载均衡 + 熔断 |
| `seedance/api.py`（**新**） | 火山方舟 Seedance ARK 原生 API 客户端 |
| `seedance/_debug.py`（**新**） | Dry-Run / 日志脱敏工具 |
| `seedance/providers/ark.py`（**新**） | ARK 官方 / 网关双模 |
| `seedance/providers/gateway.py`（**新**） | 聚合网关（带 Idempotency-Key） |
| `seedance/providers/runninghub.py`（**新**） | RunningHub（媒体须公网 URL） |

### 3.3 Pipeline 定义 `utils/resource/pipelines/`

| 目录 | YAML 文件 | 工作流 JSON |
|------|----------|------------|
| `imagegen/` | `qwen_2512.yaml`（声明式） | `qwen_2512.json` |
| `imagegen/` | `qwen_2511.yaml`（编程式：图片编辑） | `qwen_edit_2511.json` |
| `imagegen/` | `anima1.yaml`（声明式：RH App） | workflow 字段为 webappId |
| `imagegen/` | `banana2.yaml` / `banana_pro.yaml`（编程式：GPT-Image2） | — |
| `imagegen/` | `gpt_image2.yaml`（编程式：通用 OpenAI 兼容） | — |
| `imagegen/` | `minimax_image01.yaml`（编程式：MiniMax 文生图） | — |
| `videogen/` | `wan2.2_videogen.yaml`（编程式：统一视频 mapper） | `wan2.2_t2v.json` + `wan2.2_i2v.json`（mapper 动态切换） |
| `videogen/` | `seedance2.yaml` / `seedance2_fast.yaml` / `seedance15_pro.yaml`（编程式：Seedance 多供应商） | — |
| `music/` | `ace_step1.5.yaml`（编程式） | `ace_step1.5.json` |
| `speech/` | `IndexTTS2.yaml`（编程式） | `IndexTTS2.json` |
| `speech/` | `minimax_t2a_speech.yaml`（编程式） | — |
| `speech/` | `mimo_tts.yaml`（编程式） | — |

> ⚠ **v2.x 变化**：旧版的 `text2image/` / `image2image/` / `image_edit/` / `text2video/` / `image2video/` 目录已合并为 `imagegen/` / `videogen/`，具体形态由 NodeDef 的 `inputs` 端口规格决定。

### 3.4 映射函数 `utils/mappers/`

| 文件 | 函数 |
|------|------|
| `image2image.py` | `qwen_img2img_mapper`（保留历史；新版已不再使用独立 image2image 节点） |
| `image_edit.py` | `qwen_edit_mapper` |
| `gpt_image2.py` | `gpt_image2_mapper`（**返回 `NodeOutput`**） |
| `minimax_text2image.py` | `minimax_image01_mapper` |
| `minimax_image2image.py` | `minimax_image01_img2img_mapper` |
| `minimax_speech.py` | `minimax_t2a_speech_mapper`（含 voice_id 缓存） |
| `mimo_speech.py` | `mimo_tts_mapper` |
| `video.py` | `wan_videogen_mapper`（**统一文生/图生 mapper，按图片数自动选 workflow**） + `wan_text2video_mapper` / `wan_img2video_mapper` 别名 + `interpolate_prompt_refs` |
| `music.py` | `ace_step_mapper` |
| `speech.py` | `index_tts2_mapper` |
| `seedance.py`（**新**） | `seedance_video_mapper` / `seedance_text2video_mapper` / `seedance_draft_mapper`（YAML 兼容入口；实际执行走 `SeedanceAdapter`） |

### 3.5 业务子模块

| 模块 | 职责 | 关键变化 |
|------|------|---------|
| `rh_generate/__init__.py` | 统一命令：生图/改图/生视频/生音乐/生语音/模型列表/模型详情 | + 失败自动退还积分 + 进度回调包装 + `ev.image_id_list` 多图入口 |
| `rh_generate/_knowledge.py` | AI 知识库注册 | 改用 `ai_entity(KnowledgePoint(...))` |
| `rh_help/__init__.py` | 帮助模块 | 注册到全局帮助一览 |
| `rh_admin/__init__.py` | 积分管理命令 | 命令同时支持 `@ai_tools` |
| `rh_admin/commands.py` | 积分管理核心逻辑 + AI Tools | — |
| `rh_agent/__init__.py`（**强化**） | AIGC 创作能力代理画像 | 模块加载时立即注册 `rh_aigc_agent`；工具白名单 + 关键词匹配 |
| `rh_models/__init__.py`（**新**） | 模型清单命令触发器 | `模型列表` / `模型清单` / `可用模型`，支持按任务类型过滤 |
| `rh_models/api.py`（**新**） | 模型目录聚合（`ModelEntry` / `build_model_catalog()`） | `include_unavailable` / `task_type` / `as_text` 参数；按 `backend_model` 去重 |
| `rh_models/utils.py`（**新**） | 文本格式化 + `ai_list_models()` | — |
| `rh_models/webapi.py`（**新**） | FastAPI 路由 `/RH_ComfyUI/models` | `__init__.py` 顶部 `from . import webapi` 触发挂载 |
| `rh_config/comfyui_config.py` | `SERVICE_CONFIG` + `PLUGIN_CONFIG` 双实例 | 取代旧的 `RHCOMFYUI_CONFIG` |
| `rh_config/service_config.py`（**新**） | 上游服务连接（API Key / BaseURL / Seedance 三供应商） | 按 `GsDivider` 分组 |
| `rh_config/plugin_config.py`（**新**） | 插件行为（并发 + 积分规则） | — |
| `utils/points.py` | 积分检查 | 模块常量保留兼容，业务实际用 `node.point_cost` |
| `utils/image_process.py`（**新**） | 图片预处理 | `resize_long_edge` / `correct_orientation` / `preprocess_for_video` |

---

## 四、删除的旧文件（v2.0 重构时）

| 旧文件 | 删除原因 | 替代 |
|--------|---------|------|
| `rh_draw/__init__.py` | 合并到 `rh_generate` | `rh_generate/__init__.py` |
| `rh_video/__init__.py` | 合并到 `rh_generate` | `rh_generate/__init__.py` |
| `rh_audio/__init__.py` | 合并到 `rh_generate` | `rh_generate/__init__.py` |
| `utils/models/` 整个目录 | 被 `utils/core/` + `utils/backends/` 替代 | `pipeline.py` + `base.py` |
| `utils/workflow.py` | 被 `PipelineRegistry` 替代 | `pipeline.py` |
| `utils/wrapper.py` | 被 `router.py` + `executor.py` + 各业务模块替代 | 核心层 + AI工具 |
| `utils/comfyui/_request.py` | 被 `mappers/` + `pipelines/` 替代 | 声明式YAML + 映射函数 |
| `utils/blt/` 整个目录 | **v2.x 重命名为 `gpt_image2/`**（BLT 是其中一种兼容服务） | `GPTImage2API` + `GPTImage2Adapter` |
| `utils/comfyui/` 整个目录 | 被 `backends/comfyui/` 替代 | `ComfyUIAPI` + `ComfyUIAdapter` |
| `utils/RH/` 整个目录 | 预留后端，暂未实现 | 未来 `backends/rh/` |
| `utils/ai_tools.py`（v2.0 中期删除） | AI Tools 已迁入 `rh_admin/commands.py` 的 `@ai_tools` 装饰器 | 各业务模块内联 |

> ⚠ **v2.x 变化**：v2.0 重构时把 `utils/blt/` 重命名为 `utils/backends/gpt_image2/`（语义更广，BLT 只是 OpenAI 兼容服务之一）。

---

## 五、关键设计决策（v2.0 + v2.x 演进）

### 5.1 命令设计：`[模型名]` 可选参数（v2.0）

```
rh生图 一只可爱的猫咪          → 自动路由
rh生图 qwen 一只可爱的猫咪     → 强制使用 qwen_2512
rh生图 banana 一只可爱的猫咪   → 模糊匹配 → banana2
```

> ⚠ **v2.x 增强**：`parse_model_from_prompt()` 现在只返回 token，由 Router 在用户指定失败时做**输入感知的部分名匹配**——避免「带图场景下 `qwen` 被错配到纯文生图的 `qwen_2512`」。

### 5.2 图片输入自动推断（v2.0）

旧版按任务类型自动切换：

```
rh生图 一只猫          → text2image
rh生图 一只猫 [图片]    → image2image
rh生视频 日出风景       → text2video
rh生视频 日出风景 [图片] → image2video
```

**v2.x 收编后**（4 种 TaskType + 输入档案）：

```
rh生图 一只猫          → IMAGE 模态,0 张图 → Router 选中 qwen_2512 (文生图)
rh生图 一只猫 [图片]    → IMAGE 模态,1 张图 → Router 选中 qwen_2511 (编辑)
rh生视频 日出风景       → VIDEO 模态,0 张图 → Router 选中 wan2.2_videogen 或 seedance2
rh生视频 日出风景 [图片] → VIDEO 模态,1 张图 → I2V
rh生视频 +2 张图        → VIDEO 模态,2 张图 → 首尾帧(交给 Seedance)
```

### 5.3 Pipeline 两种映射模式（v2.0）

| 模式 | 适用场景 | 示例 |
|------|---------|------|
| **declarative** | 简单工作流，只需填入 prompt/width/height | `qwen_2512.yaml` |
| **programmatic** | 复杂工作流（图片上传 / 多图 / 节点链接 / 工作流覆盖 / 多供应商分发） | `qwen_2511_edit.yaml`、`wan2.2_videogen.yaml`、`seedance2.yaml` |

### 5.4 智能路由策略（v2.0）

```
1. 用户指定 model → 精确/输入感知部分名匹配
2. 输入档案过滤 (NodeDef.inputs)
3. 可用性过滤 → 过滤掉不可用后端
4. AI Agent 推荐 → 根据 prompt + knowledge_content 推荐
5. 优先级兜底 → 按 capabilities.priority 排序,组内随机
```

**v2.x 增强**：路由不再硬编码任何后端列表——Adapter 通过 `capabilities()` 自报家门（`supported_tasks` / `priority` / `mode`），Router 用 `pipeline_registry.get_by_task()` 圈桶，再用 `_node_supports_request()` 做输入档案过滤。

### 5.5 双入口对等（v2.0）

每个生成能力同时通过两种方式暴露：

| 入口 | 方式 | 最终调用 |
|------|------|---------|
| 命令触发器 | `@sv.on_command("生图", to_ai="...")` | `route() → execute_generation()` |
| AI 工具 | `@ai_tools` 函数（`rh_admin/commands.py`） | `route() → execute_generation()` |

**v2.x 增强**：新增 `rh_agent/__init__.py` 的能力代理画像（`register_rh_aigc_agent()`），让 AI Agent Mesh 通过关键词 + 工具白名单自动选择代理；新增 `rh_models` 模块提供 FastAPI + AI 工具入口。

### 5.6 Seedance Spec-Provider 两层解耦（v2.x 新）

```
GenerationRequest
    │  classify_video_spec()
    ▼
VideoGenSpec (供应商无关)
    │  shape / media / ordered_segments / params
    │  SeedanceProvider.render_create(spec, model)
    ▼
(method, url, headers, body) (供应商专属 HTTP 请求)
    │  poll_until_done()
    ▼
NormalizedTask (供应商无关的统一结果)
    │  SeedanceAdapter 包装
    ▼
NodeOutput
```

- 三家供应商（ARK / Gateway / RunningHub）共享同一 Spec
- 每家只需实现 `render_create` / `parse_create` / `get` 三个方法
- 自动在多供应商间负载均衡 + 熔断

---

## 六、新增工作流示例（v2.x 适配）

新增 Seedance 视频节点只需一步，零 Python 改动：

```yaml
# utils/resource/pipelines/videogen/seedance2.yaml
name: seedance2
display_name: Seedance 2.0
task_type: video
backend: seedance
backend_model: doubao-seedance-2-0-260128          # ark 默认模型
backend_models:                                       # 多供应商映射
  ark: doubao-seedance-2-0-260128
  gateway: dreamina-seedance-2.0
  runninghub: ""
point_cost: 20

mode: programmatic
mapper: "RH_ComfyUI.utils.mappers.seedance:seedance_video_mapper"

capabilities:
  priority: 90
  mode: async_poll

inputs:
  prompt:        { type: text, required: true }
  images:        { type: list, item_type: image, min_items: 0, max_items: 9 }
  video_refs:    { type: list, item_type: video, max_items: 3 }
  audio_refs:    { type: list, item_type: audio, max_items: 3 }
  ratio:         { type: enum, values: ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"], default: "adaptive" }
  resolution:    { type: enum, values: ["480p", "720p", "1080p"], default: "720p" }
  duration:      { type: integer, default: 5, minimum: 4, maximum: 15 }
  seed:          { type: integer, required: false }
  generate_audio: { type: boolean, default: false }
  watermark:      { type: boolean, default: false }
  camera_fixed:   { type: boolean, default: false }
  return_last_frame: { type: boolean, default: false }

outputs:
  video:      { type: output_video }
  last_frame: { type: output_image }
```

**在 Web 控制台启用对应供应商即可使用**——无需修改 Python 代码。

---

## 七、当前目录结构（v2.x）

```
RH_ComfyUI/                              # 插件根目录
├── __init__.py                          # 外层入口（force_prefix 等）
├── __nest__.py                          # 嵌套加载标记
├── pyproject.toml                       # v2.0.0, +pyyaml依赖
├── README.md
├── LICENSE
├── ICON.png
│
└── RH_ComfyUI/                          # 内层包
    ├── __init__.py                      # Plugins + @on_core_start + 导入 rh_models 触发路由挂载
    ├── __full__.py
    ├── version.py                       # v2.0.0
    │
    ├── rh_generate/                     # 🆕 统一生成命令
    │   ├── __init__.py                  # SV + on_command (含 to_ai)
    │   └── _knowledge.py               # AI 知识库注册(ai_entity)
    │
    ├── rh_admin/                        # 积分管理
    │   ├── __init__.py
    │   └── commands.py
    │
    ├── rh_agent/                        # 🆕 AIGC 能力代理画像
    │   └── __init__.py
    │
    ├── rh_help/                         # 🆕 帮助模块
    │   └── __init__.py
    │
    ├── rh_models/                       # 🆕 模型清单(命令+FastAPI+AI)
    │   ├── __init__.py                  # SV 命令触发器
    │   ├── api.py                       # ModelEntry + build_model_catalog
    │   ├── utils.py                     # 文本格式化 + ai_list_models
    │   └── webapi.py                    # /RH_ComfyUI/models FastAPI 路由
    │
    ├── rh_config/                       # 配置（拆分双实例）
    │   ├── __init__.py
    │   ├── comfyui_config.py            # SERVICE_CONFIG + PLUGIN_CONFIG
    │   ├── service_config.py            # 上游服务连接
    │   └── plugin_config.py             # 插件行为
    │
    └── utils/
        ├── core/                        # 🆕 核心架构层
        │   ├── __init__.py
        │   ├── request.py               # GenerationRequest / GenerationResult (4种TaskType + 归一化)
        │   ├── pipeline.py              # NodeDef / PipelineRegistry
        │   ├── router.py                # route() 智能路由
        │   ├── executor.py              # execute_generation() + 落盘
        │   ├── parser.py                # parse_model_from_prompt + parse_mood_from_prompt
        │   └── types.py                 # 🆕 PortSpec/MediaRef/ContentItem/CapabilityManifest/NodeOutput
        │
        ├── backends/                    # 🆕 后端抽象层(6 个 Adapter)
        │   ├── __init__.py              # AdapterRegistry + init_backends()
        │   ├── base.py                  # Adapter ABC
        │   ├── comfyui/
        │   │   ├── __init__.py
        │   │   ├── api.py              # ComfyUIAPI (含 set_workflow_override)
        │   │   └── executor.py         # ComfyUIAdapter
        │   ├── gpt_image2/
        │   │   ├── __init__.py
        │   │   ├── api.py              # GPTImage2API
        │   │   └── executor.py         # GPTImage2Adapter
        │   ├── rh_app/
        │   │   ├── __init__.py
        │   │   ├── api.py              # RHAppAPI
        │   │   └── executor.py         # RHAppAdapter
        │   ├── minimax/
        │   │   ├── __init__.py
        │   │   ├── api.py              # MiniMaxAPI
        │   │   └── executor.py         # MiniMaxAdapter
        │   ├── mimo/
        │   │   ├── __init__.py
        │   │   ├── api.py              # MIMOAPI
        │   │   └── executor.py         # MIMOAdapter
        │   └── seedance/                # 🆕 Seedance 多供应商
        │       ├── __init__.py
        │       ├── executor.py         # SeedanceAdapter (负载均衡+熔断+Dry-Run)
        │       ├── spec.py             # VideoTaskShape/MediaRole/VideoGenSpec
        │       ├── classify.py         # classify_video_spec()
        │       ├── provider.py         # SeedanceProvider ABC + NormalizedTask
        │       ├── registry.py         # 供应商选择+熔断
        │       ├── api.py              # SeedanceAPI (ARK 原生)
        │       ├── _debug.py           # Dry-Run/日志脱敏
        │       └── providers/
        │           ├── __init__.py
        │           ├── ark.py          # ArkSeedanceProvider
        │           ├── gateway.py      # GatewaySeedanceProvider
        │           └── runninghub.py   # RunningHubSeedanceProvider
        │
        ├── mappers/                     # 🆕 编程式映射函数
        │   ├── __init__.py
        │   ├── image_edit.py           # qwen_edit_mapper
        │   ├── image2image.py          # qwen_img2img_mapper(历史)
        │   ├── gpt_image2.py           # gpt_image2_mapper
        │   ├── minimax_text2image.py   # minimax_image01_mapper
        │   ├── minimax_image2image.py  # minimax_image01_img2img_mapper
        │   ├── minimax_speech.py       # minimax_t2a_speech_mapper
        │   ├── mimo_speech.py          # mimo_tts_mapper
        │   ├── video.py                # wan_videogen_mapper + interpolate_prompt_refs
        │   ├── music.py                # ace_step_mapper
        │   ├── speech.py               # index_tts2_mapper
        │   └── seedance.py             # 🆕 seedance mapper (YAML 兼容入口)
        │
        ├── database/                   # 数据库
        │   ├── __init__.py
        │   └── models.py                # RHBind 积分表
        │
        ├── resource/
        │   ├── RESOURCE_PATH.py        # 路径常量(IMAGEGEN/VIDEOGEN/MUSIC/SPEECH)
        │   ├── workflow/               # 工作流 JSON
        │   └── pipelines/              # 🆕 Pipeline YAML 定义
        │       ├── imagegen/           # 🆕 统一图片生成(image2image/text2image/image_edit)
        │       │   ├── qwen_2512.yaml
        │       │   ├── qwen_2511.yaml
        │       │   ├── anima1.yaml
        │       │   ├── banana2.yaml
        │       │   ├── banana_pro.yaml
        │       │   ├── gpt_image2.yaml
        │       │   └── minimax_image01.yaml
        │       ├── videogen/           # 🆕 统一视频生成(text2video/image2video/首尾帧/多模态)
        │       │   ├── wan2.2_videogen.yaml
        │       │   ├── seedance2.yaml
        │       │   ├── seedance2_fast.yaml
        │       │   └── seedance15_pro.yaml
        │       ├── music/              # 音乐
        │       │   └── ace_step1.5.yaml
        │       └── speech/             # 语音
        │           ├── IndexTTS2.yaml
        │           ├── minimax_t2a_speech.yaml
        │           └── mimo_tts.yaml
        │
        ├── points.py                   # 积分检查
        └── image_process.py            # 🆕 图片预处理
```

---

## 八、迁移对照表（v2.0 旧架构 → v2.x 新架构）

| 维度 | 旧（v2.0 初版） | 新（v2.x 现行） |
|------|--------------|-------------|
| 任务类型 | 7 种（text2image/image2image/image_edit/text2video/image2video/music/speech） | 4 种（IMAGE/VIDEO/MUSIC/SPEECH），旧值由 `normalize_task_type()` 归一化 |
| Pipeline 数据类 | `PipelineDef` | `NodeDef`（`PipelineDef` 作为别名） |
| Pipeline 字段 | name/display_name/task_type/backend/point_cost/mode/mappings | + `backend_model` / `backend_models` / `provider` / `inputs` / `outputs` / `capabilities` |
| 后端基类 | `Backend`（3 方法） | `Adapter`（5 成员，`Backend = Adapter`） |
| 后端实现 | 5 个（ComfyUI / GPT-Image2 / RH App / MiniMax / MiMo） | + **Seedance** |
| GPT-Image2 标识 | `gpt_image2` | `gpt-image-2`（连字符） |
| 节点输出 | `GenerationResult` | `NodeOutput`（`GenerationResult.from_node_output()` 兼容） |
| 端口类型 | 无显式声明 | `PortSpec` / `PortType`（驱动 Router 输入档案匹配） |
| 多模态内容 | 无 | `MediaRef` / `ContentItem` / `ordered_content` |
| 进度上报 | 无 | `ProgressEvent` + `on_progress` 回调 |
| 能力声明 | `PRIORITY` 字典（硬编码） | `CapabilityManifest`（自报家门） |
| 路由 | 按细分任务类型圈桶 | 按 4 种输出模态圈桶 + 输入档案过滤 |
| 配置 | `RHCOMFYUI_CONFIG` 单实例 | `SERVICE_CONFIG` + `PLUGIN_CONFIG` 双实例 |
| 积分检查 | `_do_generate()` 用模块常量 | `_do_generate()` 用 `node.point_cost` |
| 失败处理 | 仅记录日志 | **自动退还积分** + AI 友好错误 |
| YAML 路径 | `text2image/` + `image2image/` + `image_edit/` | `imagegen/` |
| YAML 路径 | `text2video/` + `image2video/` | `videogen/` |
| 工作流覆盖 | 无 | `set_workflow_override()` |
| AI 工具 | `utils/ai_tools.py` | 各业务模块内联 `@ai_tools` |
| 模型清单 | 仅 `rh_generate.list_models` | + `rh_models` 模块（命令 + FastAPI + AI 工具） |
| Seedance | 不存在 | 独立子模块（Spec/Classify/Provider/Registry/Debug） |

---

## 九、依赖变更

`pyproject.toml` 依赖（v2.0 + v2.x 稳定）：

```toml
dependencies = [
    "httpx>=0.25.0",
    "pillow>=10.1.0",
    "aiofiles>=23.2.1",
    "aiohttp>=3.8.6",
    "websockets>=12.0",    # ComfyUI WS 连接
    "pyyaml>=6.0",         # Pipeline YAML 解析
]
```

Python 版本要求从 `>=3.8.1` 提升到 `>=3.10`（使用 `match` 语法和现代类型注解）。