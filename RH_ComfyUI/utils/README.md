# utils — 共享工具模块

## 模块概述

`utils` 是 RH_ComfyUI 插件的**共享工具层**，包含核心引擎、后端抽象、参数映射、数据库模型和资源路径管理等基础设施。所有业务子模块都依赖此层提供的能力。

## 文件结构

```
utils/
├── ai_tools.py           # AI 工具函数（已迁移至 to_ai=，文件保留为空）
├── points.py             # 积分检查与扣除逻辑
├── core/                 # 核心引擎层（请求模型、Pipeline注册表、路由、执行器、解析器）
├── backends/             # 后端抽象层（ComfyUI、BLT、RH App 三个后端实现）
├── mappers/              # 编程式参数映射函数
├── database/             # 数据库模型（积分表）
└── resource/             # 资源路径管理 + Pipeline YAML 定义 + 工作流 JSON
```

## 子模块概览

| 子模块 | 职责 | 详见 |
|--------|------|------|
| [`core/`](RH_ComfyUI/utils/core/README.md) | 核心引擎：统一请求模型、Pipeline注册表、智能路由、执行器、命令解析 | 核心架构文档 |
| [`backends/`](RH_ComfyUI/utils/backends/README.md) | 后端抽象：Backend 基类 + ComfyUI/BLT/RH App 三个实现 | 后端架构文档 |
| [`mappers/`](RH_ComfyUI/utils/mappers/README.md) | 参数映射：将 GenerationRequest 转换为各后端的工作流参数 | 映射器文档 |
| [`database/`](RH_ComfyUI/utils/database/README.md) | 数据模型：RHBind 积分表 + Web 控制台注册 | 数据库文档 |
| [`resource/`](RH_ComfyUI/utils/resource/README.md) | 资源管理：路径常量、Pipeline YAML 定义、工作流 JSON | 资源文档 |

## 顶层工具文件

### [`points.py`](RH_ComfyUI/utils/points.py:1) — 积分检查模块

提供 [`check_point(ev, point)`](RH_ComfyUI/utils/points.py:21) 函数，用于检查用户积分是否充足并自动扣除：

```python
async def check_point(ev: Event, point: int) -> Tuple[bool, str]:
    bind = await RHBind.deduct_point(ev.user_id, ev.bot_id, point)
    now_point = await RHBind.get_point(ev.user_id, ev.bot_id)
    if bind:
        return True, f"💪 积分充足！已扣除{point}积分!\n📋 当前积分: {now_point}"
    else:
        return False, f"❌ 积分不足！需要{point}积分！\n📋 当前积分: {now_point}"
```

积分配置从 [`RHCOMFYUI_CONFIG`](RH_ComfyUI/rh_config/comfyui_config.py:6) 读取：

| 配置键 | 变量名 | 说明 |
|--------|--------|------|
| `Draw_Point` | `Draw_Point` | 绘图积分消耗 |
| `Edit_Image_Point` | `Edit_Image_Point` | 编辑图片积分消耗 |
| `Music_Point` | `Music_Point` | 音乐生成积分消耗 |
| `Speech_Point` | `Speech_Point` | 语音生成积分消耗 |
| `Video_Point` | `Video_Point` | 视频生成积分消耗 |

### [`ai_tools.py`](RH_ComfyUI/utils/ai_tools.py:1) — AI 工具函数（已废弃）

原文件中的 AI 工具函数已迁移至各命令处理器的 `to_ai=` 描述参数中，本文件保留为空壳。

## 依赖关系总览

```
utils/core/request.py    ← 定义 TaskType、GenerationRequest、GenerationResult
utils/core/pipeline.py   ← 定义 PipelineDef、PipelineRegistry
utils/core/router.py     ← 智能路由（依赖 backends 检查可用性）
utils/core/executor.py   ← 统一执行入口（分发到 backends）
utils/core/parser.py     ← 命令解析（从文本提取模型名和 prompt）
utils/backends/base.py   ← Backend 抽象基类
utils/backends/*/        ← 具体后端实现
utils/mappers/*/         ← 参数映射函数
utils/database/models.py ← RHBind 积分表
utils/resource/          ← 路径常量 + YAML/JSON 资源
```
