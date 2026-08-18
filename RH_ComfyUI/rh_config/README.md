# rh_config — 配置管理模块

## 模块概述

`rh_config` 是 RH_ComfyUI 插件的**配置管理子模块**，负责定义和管理插件的所有可配置项。使用 GsCore 的 `StringConfig` 机制，支持通过 Web 控制台可视化修改配置。

## 文件结构

```
rh_config/
├── __init__.py           # 空文件（包标记）
├── config_default.py     # 配置项定义（默认值）
└── comfyui_config.py     # StringConfig 实例创建
```

## 核心组件

### 1. 配置项定义 [`config_default.py`](RH_ComfyUI/rh_config/config_default.py:1)

定义了 [`CONFIG_DEFAULT`](RH_ComfyUI/rh_config/config_default.py:9) 字典，包含所有可配置项：

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `Max_Concurrency` | `GsIntConfig` | `1` | 全局最大并发数，限制所有后端同时执行的任务数 |
| `ComfyUI_BaseURL` | `GsStrConfig` | `"127.0.0.1:8188"` | ComfyUI 服务地址 |
| `ComfyUI_Enabled_Workflows` | `GsListStrConfig` | `[]` | 启用的 ComfyUI 工作流(模型名或 json;默认空;热读) |
| `RH_apikey` | `GsStrConfig` | `""` | RunningHub API Key |
| `Default_Point` | `GsIntConfig` | `20` | 新用户默认初始积分 |
| `OpenAI_Image_apikey` | `GsStrConfig` | `""` | OpenAI 兼容生图接口的 API Key |
| `OpenAI_Image_BaseURL` | `GsStrConfig` | `"https://api.openai.com/v1"` | OpenAI 兼容生图接口的 Base URL |
| `Draw_Point` | `GsIntConfig` | `2` | 每次绘图消耗的积分 |
| `Edit_Image_Point` | `GsIntConfig` | `4` | 每次图片编辑消耗的积分 |
| `Music_Point` | `GsIntConfig` | `2` | 每次音乐生成消耗的积分 |
| `Speech_Point` | `GsIntConfig` | `2` | 每次语音生成消耗的积分 |
| `Video_Point` | `GsIntConfig` | `15` | 每次视频生成消耗的积分 |

### 2. StringConfig 实例 [`comfyui_config.py`](RH_ComfyUI/rh_config/comfyui_config.py:1)

```python
RHCOMFYUI_CONFIG = StringConfig(
    "RHComfyUI",
    CONFIG_PATH,        # data/RHComfyUI/config.json
    CONFIG_DEFAULT,
)
```

[`RHCOMFYUI_CONFIG`](RH_ComfyUI/rh_config/comfyui_config.py:6) 是全局单例，所有需要读取配置的模块都导入此实例。

## 配置读取方式

```python
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

# 读取配置值（注意 .data）
api_key: str = RHCOMFYUI_CONFIG.get_config("RH_apikey").data
concurrency: int = RHCOMFYUI_CONFIG.get_config("Max_Concurrency").data

# 运行时修改（自动持久化到文件）
RHCOMFYUI_CONFIG.set_config("Max_Concurrency", 3)
```

## 配置影响范围

```
Max_Concurrency    ──→ utils/core/executor.py（全局 Semaphore 并发控制）
ComfyUI_BaseURL    ──→ utils/backends/comfyui/api.py（WebSocket/HTTP 连接地址）
ComfyUI_Enabled_Workflows ──→ backends/comfyui/config.py + AdapterChannel（工作流启用闸,热读）
RH_apikey          ──→ utils/backends/comfyui/api.py + rh_app/api.py（API 认证）
OpenAI_Image_apikey  ──→ utils/backends/gpt_image2/api.py（API 认证）
OpenAI_Image_BaseURL ──→ utils/backends/gpt_image2/api.py（API 端点）
Default_Point      ──→ utils/database/models.py（新用户初始积分）
*_Point            ──→ utils/points.py（各任务类型的积分消耗）
```

## 与其他模块的关系

```
rh_config ──→ GsCore StringConfig（配置持久化框架）
          ←── utils/backends/*（读取后端连接配置）
          ←── utils/core/executor.py（读取并发限制）
          ←── utils/points.py（读取消耗积分）
          ←── utils/database/models.py（读取默认积分）
```
