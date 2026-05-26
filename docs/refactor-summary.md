# RH_ComfyUI v2.0 重构技术总结

> **版本**: v2.0.0  
> **日期**: 2026-05-25  
> **重构范围**: 全架构重写，从硬编码模型注册表 → 声明式 Pipeline + 后端抽象 + 智能路由

---

## 一、重构动机

### 1.1 旧架构的核心问题

| # | 问题 | 严重程度 | 旧代码位置 |
|---|------|---------|-----------|
| P1 | **命令爆炸** — 每种任务类型一个命令，用户记忆负担重 | 🔴 | `rh_draw/` + `rh_video/` + `rh_audio/` |
| P2 | **无法指定模型** — 用户说"生图"后无法选择 qwen/banana | 🔴 | `wrapper.py` → `select_available_model()` |
| P3 | **工作流硬编码** — 新增工作流必须写 Python 函数 | 🔴 | `utils/comfyui/_request.py` 硬编码节点 ID |
| P4 | **后端耦合** — ComfyUI/BLT/RH 调用逻辑散落各处 | 🟡 | `utils/blt/` + `utils/comfyui/` + `utils/RH/` |
| P5 | **参数模型不统一** — 每个函数签名不同 | 🟡 | `wrapper.py` 各函数参数各异 |
| P6 | **AI 集成不完整** — 命令触发器无 `to_ai` | 🟡 | `rh_draw/__init__.py` 等 |
| P7 | **图片输入处理不统一** — 命令层和 AI 层两套逻辑 | 🟡 | `ev.image_id` vs `RM.get(image_id)` |

### 1.2 旧架构调用链

```
用户命令 → rh_draw/rh_video/rh_audio → wrapper.py → MODEL_REGISTRY(硬编码) → _request.py(硬编码节点)
AI工具   → wrapper.py @ai_tools       → 同上
```

**核心瓶颈**：所有路径汇聚到硬编码函数，新增工作流 = 新增 Python 函数 + 修改注册表 + 修改优先级。

---

## 二、新架构概览

### 2.1 三层解耦

```
┌─────────────────────────────────────────────────────┐
│              命令 / AI 入口层                          │
│   on_command + to_ai  |  @ai_tools                   │
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

### 2.2 核心原则

| 原则 | 说明 |
|------|------|
| **任务驱动，而非模型驱动** | 用户说"我要生图"，不说"我要用qwen生图" |
| **声明式优先** | 新增工作流 = 新增 YAML + JSON，零 Python 改动 |
| **Backend 可插拔** | ComfyUI / BLT / RH 都是等价后端，统一接口 |
| **双入口对等** | 命令触发器和 AI 工具访问同一套逻辑 |
| **参数统一** | 所有任务类型共用 `GenerationRequest` |

---

## 三、新增文件清单

### 3.1 核心层 `utils/core/`

| 文件 | 职责 |
|------|------|
| `request.py` | `GenerationRequest` / `GenerationResult` / `TaskType` / `OutputType` 统一参数模型 |
| `pipeline.py` | `PipelineDef` / `PipelineRegistry` — 从 YAML 自动加载 Pipeline 定义 |
| `router.py` | `route()` 智能路由 — 用户指定→可用性过滤→AI推荐→优先级兜底 |
| `executor.py` | `execute_generation()` 统一执行入口 |
| `parser.py` | `parse_model_from_prompt()` 从命令文本中提取可选模型名 |

### 3.2 后端层 `utils/backends/`

| 文件 | 职责 |
|------|------|
| `base.py` | `Backend` ABC — `check_available()` / `execute()` |
| `comfyui/api.py` | `ComfyUIAPI` — WS 连接、上传、生成（从原 `comfyui_api.py` 迁移） |
| `comfyui/executor.py` | `ComfyUIBackend` — 声明式/编程式映射 + 工作流执行 |
| `blt/api.py` | `BLTAPI` — OpenAI 兼容 API 客户端（从原 `blt_request.py` 迁移） |
| `blt/executor.py` | `BLTBackend` — 直接调 mapper_func 执行 |

### 3.3 Pipeline 定义 `utils/resource/pipelines/`

| 目录 | YAML 文件 | 工作流 JSON |
|------|----------|------------|
| `text2image/` | `qwen_2512.yaml` (声明式) | `qwen_2512.json` |
| `text2image/` | `banana2.yaml` (编程式) | — |
| `text2image/` | `banana_pro.yaml` (编程式) | — |
| `image2image/` | `qwen_2512_img2img.yaml` (编程式) | `qwen_2512_with_lora.json` |
| `image_edit/` | `qwen_2511_edit.yaml` (编程式) | `qwen_edit_2511.json` |
| `image_edit/` | `banana2_edit.yaml` (编程式) | — |
| `image_edit/` | `banana_pro_edit.yaml` (编程式) | — |
| `text2video/` | `wan2.2_text2video.yaml` (编程式) | `wan2.2_text2video.json` |
| `image2video/` | `wan2.2_img2video.yaml` (编程式) | `wan2.2_image2video.json` |
| `music/` | `ace_step1.5.yaml` (编程式) | `ace_step1.5.json` |
| `speech/` | `IndexTTS2.yaml` (编程式) | `IndexTTS2.json` |

### 3.4 映射函数 `utils/mappers/`

| 文件 | 函数 |
|------|------|
| `image2image.py` | `qwen_img2img_mapper` |
| `image_edit.py` | `qwen_edit_mapper` |
| `blt_text2image.py` | `banana2_mapper` / `banana_pro_mapper` |
| `blt_image_edit.py` | `banana2_edit_mapper` / `banana_pro_edit_mapper` |
| `video.py` | `wan_text2video_mapper` / `wan_img2video_mapper` |
| `music.py` | `ace_step_mapper` |
| `speech.py` | `index_tts2_mapper` |

### 3.5 命令模块

| 文件 | 职责 |
|------|------|
| `rh_generate/__init__.py` | 统一命令：生图/改图/生视频/生音乐/生语音/模型列表/模型详情 |
| `rh_generate/_knowledge.py` | AI 知识库注册 |
| `rh_help/__init__.py` | 帮助模块 |
| `utils/ai_tools.py` | @ai_tools 函数（与命令对等） |

---

## 四、删除的旧文件

| 旧文件 | 删除原因 | 替代 |
|--------|---------|------|
| `rh_draw/__init__.py` | 合并到 `rh_generate` | `rh_generate/__init__.py` |
| `rh_video/__init__.py` | 合并到 `rh_generate` | `rh_generate/__init__.py` |
| `rh_audio/__init__.py` | 合并到 `rh_generate` | `rh_generate/__init__.py` |
| `utils/models/` 整个目录 | 被 `utils/core/` + `utils/backends/` 替代 | `pipeline.py` + `base.py` |
| `utils/workflow.py` | 被 `PipelineRegistry` 替代 | `pipeline.py` |
| `utils/wrapper.py` | 被 `router.py` + `executor.py` + `ai_tools.py` 替代 | 核心层 + AI工具 |
| `utils/comfyui/_request.py` | 被 `mappers/` + `pipelines/` 替代 | 声明式YAML + 映射函数 |
| `utils/blt/` 整个目录 | 被 `backends/blt/` 替代 | `BLTAPI` + `BLTBackend` |
| `utils/comfyui/` 整个目录 | 被 `backends/comfyui/` 替代 | `ComfyUIAPI` + `ComfyUIBackend` |
| `utils/RH/` 整个目录 | 预留后端，暂未实现 | 未来 `backends/rh/` |

---

## 五、关键设计决策

### 5.1 命令设计：`[模型名]` 可选参数

```
rh生图 一只可爱的猫咪          → 自动路由
rh生图 qwen 一只可爱的猫咪     → 强制使用 qwen_2512
rh生图 banana 一只可爱的猫咪   → 模糊匹配 → banana2
```

解析逻辑在 `parse_model_from_prompt()` 中：提取第一个词，精确匹配→前缀匹配→包含匹配。

### 5.2 图片输入自动推断

同一命令根据是否附带图片自动切换任务类型：

```
rh生图 一只猫          → text2image
rh生图 一只猫 [图片]    → image2image
rh生视频 日出风景       → text2video
rh生视频 日出风景 [图片] → image2video
```

### 5.3 Pipeline 两种映射模式

| 模式 | 适用场景 | 示例 |
|------|---------|------|
| **declarative** | 简单工作流，只需填入 prompt/width/height | `qwen_2512.yaml` |
| **programmatic** | 复杂工作流，需要图片上传/多图/节点链接 | `qwen_2511_edit.yaml` |

声明式映射在 YAML 中直接定义 `node_id → input_key` 对应关系，零 Python 代码。
编程式映射指定 Python 函数路径，由函数完成复杂参数填充。

### 5.4 智能路由策略

```
1. 用户指定 model → 直接选
2. 可用性过滤 → 过滤掉不可用后端
3. AI Agent 推荐 → 根据 prompt + knowledge_content 推荐
4. 优先级兜底 → 按 PRIORITY 字典排序
5. 随机选择 → 最后兜底
```

### 5.5 双入口对等

每个生成能力同时通过两种方式暴露：

| 入口 | 方式 | 最终调用 |
|------|------|---------|
| 命令触发器 | `@sv.on_command("生图", to_ai="...")` | `route() → execute_generation()` |
| AI 工具 | `@ai_tools` 函数 | `route() → execute_generation()` |

---

## 六、新增工作流示例

新增 Flux 文生图只需两步，零 Python 代码：

**Step 1**: 放工作流 JSON 到 `utils/resource/pipelines/text2image/flux.json`

**Step 2**: 创建 `utils/resource/pipelines/text2image/flux.yaml`：

```yaml
name: "flux"
display_name: "Flux Dev"
task_type: text2image
backend: comfyui
point_cost: 3
description: "Flux 模型，高质量图像生成"
knowledge_content: |
  Flux Dev 模型，擅长高质量写实图像生成。
requirements:
  - comfyui_url
workflow: "flux.json"
mode: declarative
mappings:
  prompt:
    node_id: "6"
    input_key: "text"
  width:
    node_id: "5"
    input_key: "width"
    default: 1024
  height:
    node_id: "5"
    input_key: "height"
    default: 1024
```

重启后自动注册，用户可以 `rh生图 flux 一只写实的猫` 或让 AI 自动推荐。

---

## 七、新目录结构

```
RH_ComfyUI/                              # 插件根目录
├── __init__.py                          # 外层入口
├── __nest__.py                          # 嵌套加载标记
├── pyproject.toml                       # v2.0.0, +pyyaml依赖
├── README.md
├── LICENSE
├── ICON.png
│
└── RH_ComfyUI/                         # 内层包
    ├── __init__.py                      # Plugins + @on_core_start 初始化
    ├── __full__.py
    ├── version.py                       # v2.0.0
    │
    ├── rh_generate/                     # 🆕 统一生成命令
    │   ├── __init__.py                  # SV + on_command (含 to_ai)
    │   └── _knowledge.py               # AI 知识库注册
    │
    ├── rh_admin/                        # 积分管理（保留）
    │   ├── __init__.py
    │   └── commands.py
    │
    ├── rh_config/                       # 配置（保留）
    │   ├── __init__.py
    │   ├── config_default.py
    │   └── comfyui_config.py
    │
    ├── rh_help/                         # 🆕 帮助模块
    │   └── __init__.py
    │
    └── utils/
        ├── core/                        # 🆕 核心架构层
        │   ├── __init__.py
        │   ├── request.py               # GenerationRequest / GenerationResult
        │   ├── pipeline.py              # PipelineDef / PipelineRegistry
        │   ├── router.py                # route() 智能路由
        │   ├── executor.py              # execute_generation()
        │   └── parser.py               # parse_model_from_prompt()
        │
        ├── backends/                    # 🆕 后端抽象层
        │   ├── __init__.py              # BackendRegistry + init_backends()
        │   ├── base.py                  # Backend ABC
        │   ├── comfyui/
        │   │   ├── __init__.py
        │   │   ├── api.py              # ComfyUIAPI
        │   │   └── executor.py         # ComfyUIBackend
        │   └── blt/
        │       ├── __init__.py
        │       ├── api.py              # BLTAPI
        │       └── executor.py         # BLTBackend
        │
        ├── mappers/                     # 🆕 编程式映射函数
        │   ├── __init__.py
        │   ├── image2image.py
        │   ├── image_edit.py
        │   ├── blt_text2image.py
        │   ├── blt_image_edit.py
        │   ├── video.py
        │   ├── music.py
        │   └── speech.py
        │
        ├── database/                   # 数据库（保留）
        │   ├── __init__.py
        │   └── models.py
        │
        ├── resource/
        │   ├── RESOURCE_PATH.py        # 路径常量（+PIPELINES_PATH）
        │   ├── workflow/               # 工作流 JSON（保留）
        │   └── pipelines/              # 🆕 Pipeline YAML 定义
        │       ├── text2image/
        │       ├── image2image/
        │       ├── image_edit/
        │       ├── text2video/
        │       ├── image2video/
        │       ├── music/
        │       └── speech/
        │
        ├── points.py                   # 积分检查（保留，微调）
        └── ai_tools.py                 # 🆕 @ai_tools 函数
```

---

## 八、迁移对照表

| 旧代码 | 新架构 | 变化 |
|--------|--------|------|
| `utils/models/types.py` → `ModelInfo` | `utils/core/pipeline.py` → `PipelineDef` | Python dataclass → YAML 加载 |
| `utils/models/registry.py` → `MODEL_REGISTRY` | `utils/core/pipeline.py` → `PipelineRegistry` | 硬编码 → 自动扫描 YAML |
| `utils/models/availability.py` | `utils/backends/base.py` → `Backend.check_available()` | 模型级 → 后端级检查 |
| `utils/models/priority.py` | `utils/core/router.py` → `PRIORITY` | 保留，作为兜底 |
| `utils/models/selector.py` | `utils/core/router.py` → `route()` | 合并到路由器 |
| `utils/wrapper.py` → `select_available_model()` | `utils/core/router.py` → `route()` | 统一路由 |
| `utils/wrapper.py` → `@ai_tools` 函数 | `utils/ai_tools.py` → `@ai_tools` 函数 | 重写，调用新架构 |
| `utils/comfyui/_request.py` | `utils/mappers/` + `utils/resource/pipelines/` | 硬编码 → 声明式+映射 |
| `utils/blt/request.py` | `utils/mappers/blt_*.py` | 同上 |
| `utils/workflow.py` | `utils/core/pipeline.py` → `PipelineRegistry` | 动态字典 → 注册表 |
| `rh_draw/__init__.py` | `rh_generate/__init__.py` → `generate_image()` | 合并+加 to_ai |
| `rh_video/__init__.py` | `rh_generate/__init__.py` → `generate_video()` | 合并+加 to_ai |
| `rh_audio/__init__.py` | `rh_generate/__init__.py` → `generate_music/speech()` | 合并+加 to_ai |

---

## 九、依赖变更

`pyproject.toml` 新增依赖：

```toml
dependencies = [
    "httpx>=0.25.0",
    "pillow>=10.1.0",
    "aiofiles>=23.2.1",
    "aiohttp>=3.8.6",
    "websockets>=12.0",    # 新增：ComfyUI WS 连接
    "pyyaml>=6.0",         # 新增：Pipeline YAML 解析
]
```

Python 版本要求从 `>=3.8.1` 提升到 `>=3.10`（使用 `match` 语法和现代类型注解）。
