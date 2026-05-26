# RH_ComfyUI 模块参考手册

## 目录

- [一、插件根目录](#一插件根目录)
- [二、业务子模块](#二业务子模块)
- [三、utils/core — 核心引擎](#三utilscore--核心引擎)
- [四、utils/backends — 后端实现](#四utilsbackends--后端实现)
- [五、utils/mappers — 参数映射](#五utilsmappers--参数映射)
- [六、utils/database — 数据模型](#六utilsdatabase--数据模型)
- [七、utils/resource — 资源管理](#七utilsresource--资源管理)

---

## 一、插件根目录

### [`RH_ComfyUI/__init__.py`](RH_ComfyUI/__init__.py:1) — 插件入口

**职责：** 注册插件、定义启动钩子

```python
# 插件注册
Plugins(name="RH_ComfyUI", force_prefix=["rh", "cf", "RH"], allow_empty_prefix=False)

# 启动钩子
@on_core_start
async def init_pipeline_system():
    init_backends()                                    # 注册后端
    pipeline_registry.load_from_directory(...)         # 加载 Pipeline
    register_pipeline_knowledge()                      # 注册 AI 知识库
```

### [`RH_ComfyUI/version.py`](RH_ComfyUI/version.py:1) — 版本号

```python
RH_ComfyUI_version = "2.0.0"
```

### [`__init__.py`](__init__.py:1) / [`__nest__.py`](__nest__.py:1) — 嵌套加载标记

空文件，标记 GsCore 使用嵌套加载模式。

### [`pyproject.toml`](pyproject.toml:1) — 项目元数据

```toml
[project]
name = "RH_ComfyUI"
version = "2.0.0"
dependencies = [
    "httpx>=0.25.0",
    "pillow>=10.1.0",
    "aiofiles>=23.2.1",
    "aiohttp>=3.8.6",
    "websockets>=12.0",
    "pyyaml>=6.0",
]
```

---

## 二、业务子模块

### [`rh_generate/`](RH_ComfyUI/rh_generate/) — 生成命令模块

**文件：**
- [`__init__.py`](RH_ComfyUI/rh_generate/__init__.py:1) — 命令处理器 + `_do_generate()` 通用流程
- [`_knowledge.py`](RH_ComfyUI/rh_generate/_knowledge.py:1) — AI 知识库注册

**关键函数：**

| 函数 | 行号 | 说明 |
|------|------|------|
| `_do_generate()` | [55](RH_ComfyUI/rh_generate/__init__.py:55) | 通用生成执行流程：路由→积分→执行 |
| `generate_image()` | [110](RH_ComfyUI/rh_generate/__init__.py:110) | 生图命令处理器 |
| `edit_image()` | [164](RH_ComfyUI/rh_generate/__init__.py:164) | 改图命令处理器 |
| `generate_video()` | [214](RH_ComfyUI/rh_generate/__init__.py:214) | 生视频命令处理器 |
| `generate_music()` | [259](RH_ComfyUI/rh_generate/__init__.py:259) | 生音乐命令处理器 |
| `generate_speech()` | [295](RH_ComfyUI/rh_generate/__init__.py:295) | 生语音命令处理器 |
| `list_models()` | [321](RH_ComfyUI/rh_generate/__init__.py:321) | 模型列表命令 |
| `model_detail()` | [350](RH_ComfyUI/rh_generate/__init__.py:350) | 模型详情命令 |
| `register_pipeline_knowledge()` | [11](RH_ComfyUI/rh_generate/_knowledge.py:11) | 将 Pipeline 注册为 AI 知识库 |

### [`rh_admin/`](RH_ComfyUI/rh_admin/) — 积分管理模块

**文件：**
- [`__init__.py`](RH_ComfyUI/rh_admin/__init__.py:1) — 命令处理器
- [`commands.py`](RH_ComfyUI/rh_admin/commands.py:1) — 核心逻辑 + AI Tools

**关键函数：**

| 函数 | 文件:行号 | 说明 |
|------|----------|------|
| `add_points()` | `__init__.py:29` | 增加积分命令处理器 |
| `deduct_points()` | `__init__.py:54` | 减少积分命令处理器 |
| `query_points()` | `__init__.py:79` | 查询积分命令处理器 |
| `parse_add_points_args()` | `commands.py:22` | 解析增减积分参数 |
| `parse_query_points_args()` | `commands.py:60` | 解析查询积分参数 |
| `check_pm()` | `commands.py:92` | 管理员权限校验 |
| `add_user_points()` | `commands.py:107` | 增加积分（@ai_tools） |
| `deduct_user_points()` | `commands.py:141` | 扣除积分（@ai_tools） |
| `query_user_points()` | `commands.py:184` | 查询积分（@ai_tools） |

### [`rh_agent/`](RH_ComfyUI/rh_agent/) — AI 代理注册模块

**文件：** [`__init__.py`](RH_ComfyUI/rh_agent/__init__.py:1)

**关键组件：**

| 组件 | 行号 | 说明 |
|------|------|------|
| `RH_AIGC_AGENT_PROMPT` | [16](RH_ComfyUI/rh_agent/__init__.py:16) | AIGC 创作代理系统提示词 |
| `register_rh_aigc_agent()` | [75](RH_ComfyUI/rh_agent/__init__.py:75) | 注册能力代理画像 |

### [`rh_config/`](RH_ComfyUI/rh_config/) — 配置管理模块

**文件：**
- [`config_default.py`](RH_ComfyUI/rh_config/config_default.py:1) — 配置项定义
- [`comfyui_config.py`](RH_ComfyUI/rh_config/comfyui_config.py:1) — StringConfig 实例

**配置项：** 11个（见 [`rh_config/README.md`](RH_ComfyUI/rh_config/README.md)）

### [`rh_help/`](RH_ComfyUI/rh_help/) — 帮助模块

**文件：** [`__init__.py`](RH_ComfyUI/rh_help/__init__.py:1)

---

## 三、utils/core — 核心引擎

### [`request.py`](RH_ComfyUI/utils/core/request.py:1) — 请求/响应模型

| 组件 | 行号 | 说明 |
|------|------|------|
| `TaskType` | [10](RH_ComfyUI/utils/core/request.py:10) | 7种任务类型枚举 |
| `OutputType` | [22](RH_ComfyUI/utils/core/request.py:22) | 3种输出类型枚举 |
| `TASK_OUTPUT_MAP` | [31](RH_ComfyUI/utils/core/request.py:31) | 任务类型→输出类型映射 |
| `TASK_MIME_MAP` | [42](RH_ComfyUI/utils/core/request.py:42) | 任务类型→MIME类型映射 |
| `TASK_DISPLAY_NAME` | [53](RH_ComfyUI/utils/core/request.py:53) | 任务类型→中文名映射 |
| `GenerationRequest` | [64](RH_ComfyUI/utils/core/request.py:64) | 统一请求模型（dataclass） |
| `GenerationResult` | [123](RH_ComfyUI/utils/core/request.py:123) | 统一响应模型（dataclass） |

### [`pipeline.py`](RH_ComfyUI/utils/core/pipeline.py:1) — Pipeline 注册表

| 组件 | 行号 | 说明 |
|------|------|------|
| `PipelineDef` | [17](RH_ComfyUI/utils/core/pipeline.py:17) | Pipeline 定义数据类 |
| `PipelineRegistry` | [41](RH_ComfyUI/utils/core/pipeline.py:41) | 注册表类 |
| `PipelineRegistry.load_from_directory()` | [48](RH_ComfyUI/utils/core/pipeline.py:48) | 从目录加载 YAML |
| `PipelineRegistry._load_yaml()` | [56](RH_ComfyUI/utils/core/pipeline.py:56) | 解析单个 YAML |
| `PipelineRegistry.register()` | [97](RH_ComfyUI/utils/core/pipeline.py:97) | 注册 Pipeline |
| `PipelineRegistry.get()` | [101](RH_ComfyUI/utils/core/pipeline.py:101) | 按名称查找 |
| `PipelineRegistry.get_by_task()` | [104](RH_ComfyUI/utils/core/pipeline.py:104) | 按任务类型查找 |
| `PipelineRegistry.find_by_partial_name()` | [110](RH_ComfyUI/utils/core/pipeline.py:110) | 模糊匹配 |
| `pipeline_registry` | [132](RH_ComfyUI/utils/core/pipeline.py:132) | 全局单例 |

### [`router.py`](RH_ComfyUI/utils/core/router.py:1) — 智能路由器

| 组件 | 行号 | 说明 |
|------|------|------|
| `ModelUnavailableError` | [14](RH_ComfyUI/utils/core/router.py:14) | 模型不可用异常 |
| `PRIORITY` | [24](RH_ComfyUI/utils/core/router.py:24) | 优先级配置 |
| `route()` | [35](RH_ComfyUI/utils/core/router.py:35) | 主路由函数 |
| `_ensure_runtime_initialized()` | [114](RH_ComfyUI/utils/core/router.py:114) | 懒加载兜底 |
| `_ai_recommend()` | [136](RH_ComfyUI/utils/core/router.py:136) | AI 推荐模型 |

### [`executor.py`](RH_ComfyUI/utils/core/executor.py:1) — 统一执行器

| 组件 | 行号 | 说明 |
|------|------|------|
| `_get_semaphore()` | [16](RH_ComfyUI/utils/core/executor.py:16) | 获取全局并发信号量 |
| `execute_generation()` | [30](RH_ComfyUI/utils/core/executor.py:30) | 统一执行入口 |

### [`parser.py`](RH_ComfyUI/utils/core/parser.py:1) — 命令解析器

| 组件 | 行号 | 说明 |
|------|------|------|
| `parse_model_from_prompt()` | [31](RH_ComfyUI/utils/core/parser.py:31) | 解析模型名和 prompt |

---

## 四、utils/backends — 后端实现

### [`base.py`](RH_ComfyUI/utils/backends/base.py:1) — Backend 抽象基类

| 方法 | 行号 | 说明 |
|------|------|------|
| `check_available()` | [24](RH_ComfyUI/utils/backends/base.py:24) | 检查后端是否可用 |
| `get_unavailable_reason()` | [29](RH_ComfyUI/utils/backends/base.py:29) | 返回不可用原因 |
| `execute()` | [33](RH_ComfyUI/utils/backends/base.py:33) | 执行生成任务 |

### ComfyUI 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`comfyui/api.py`](RH_ComfyUI/utils/backends/comfyui/api.py:1) | `ComfyUIAPI` | WebSocket + HTTP 客户端 |
| [`comfyui/executor.py`](RH_ComfyUI/utils/backends/comfyui/executor.py:1) | `ComfyUIBackend` | ComfyUI 后端执行器 |

### BLT 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`blt/api.py`](RH_ComfyUI/utils/backends/blt/api.py:1) | `BLTAPI` | OpenAI 兼容 HTTP 客户端 |
| [`blt/executor.py`](RH_ComfyUI/utils/backends/blt/executor.py:1) | `BLTBackend` | BLT 后端执行器 |

### RH App 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`rh_app/api.py`](RH_ComfyUI/utils/backends/rh_app/api.py:1) | `RHAppAPI` | RunningHub OpenAPI v2 客户端 |
| [`rh_app/executor.py`](RH_ComfyUI/utils/backends/rh_app/executor.py:1) | `RHAppBackend` | RH App 后端执行器 |

---

## 五、utils/mappers — 参数映射

| 文件 | 映射函数 | 目标后端 | 任务类型 |
|------|---------|---------|---------|
| [`blt_text2image.py`](RH_ComfyUI/utils/mappers/blt_text2image.py:1) | `banana2_mapper`, `banana_pro_mapper` | BLT | 文生图 |
| [`blt_image_edit.py`](RH_ComfyUI/utils/mappers/blt_image_edit.py:1) | `banana2_edit_mapper`, `banana_pro_edit_mapper` | BLT | 图片编辑 |
| [`image_edit.py`](RH_ComfyUI/utils/mappers/image_edit.py:1) | `qwen_edit_mapper` | ComfyUI | 图片编辑 |
| [`image2image.py`](RH_ComfyUI/utils/mappers/image2image.py:1) | `qwen_img2img_mapper` | ComfyUI | 图生图 |
| [`video.py`](RH_ComfyUI/utils/mappers/video.py:1) | `wan_text2video_mapper`, `wan_img2video_mapper` | ComfyUI | 视频生成 |
| [`music.py`](RH_ComfyUI/utils/mappers/music.py:1) | `ace_step_mapper` | ComfyUI | 音乐生成 |
| [`speech.py`](RH_ComfyUI/utils/mappers/speech.py:1) | `index_tts2_mapper` | ComfyUI | 语音生成 |

---

## 六、utils/database — 数据模型

### [`models.py`](RH_ComfyUI/utils/database/models.py:1)

| 组件 | 行号 | 说明 |
|------|------|------|
| `DEFAULT_POINT` | [10](RH_ComfyUI/utils/database/models.py:10) | 默认初始积分（从配置读取） |
| `RHBind` | [13](RH_ComfyUI/utils/database/models.py:13) | 积分表模型（继承 Bind） |
| `RHBind.create_data()` | [18](RH_ComfyUI/utils/database/models.py:18) | 创建用户数据 |
| `RHBind.add_point()` | [47](RH_ComfyUI/utils/database/models.py:47) | 增加积分 |
| `RHBind.get_point()` | [72](RH_ComfyUI/utils/database/models.py:72) | 查询积分 |
| `RHBind.deduct_point()` | [86](RH_ComfyUI/utils/database/models.py:86) | 扣除积分 |
| `SsPushAdmin` | [114](RH_ComfyUI/utils/database/models.py:114) | Web 控制台管理页 |

---

## 七、utils/resource — 资源管理

### [`RESOURCE_PATH.py`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:1)

| 组件 | 行号 | 说明 |
|------|------|------|
| `MAIN_PATH` | [11](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:11) | 插件运行时根目录 |
| `CONFIG_PATH` | [15](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:15) | 配置文件路径 |
| `WORKFLOW_PATH` | [18](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:18) | 运行时工作流目录 |
| `PIPELINES_PATH` | [24](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:24) | 运行时 Pipeline 目录 |
| `load_workflow()` | [36](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:36) | 加载工作流 JSON + 随机化 seed |
| `init_dir()` | [48](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:48) | 初始化目录结构 |

### 内置 Pipeline YAML 文件

| 文件 | Pipeline 名 | 任务类型 | 后端 |
|------|------------|---------|------|
| `pipelines/text2image/qwen_2512.yaml` | `qwen_2512` | 文生图 | comfyui |
| `pipelines/text2image/banana2.yaml` | `banana2` | 文生图 | blt |
| `pipelines/text2image/banana_pro.yaml` | `banana_pro` | 文生图 | blt |
| `pipelines/text2image/rh_app_demo.yaml` | `anima` | 文生图 | rh_app |
| `pipelines/image2image/qwen_2512_img2img.yaml` | `qwen_2512_img2img` | 图生图 | comfyui |
| `pipelines/image_edit/qwen_2511_edit.yaml` | `qwen_2511_edit` | 图片编辑 | comfyui |
| `pipelines/image_edit/banana2_edit.yaml` | `banana2_edit` | 图片编辑 | blt |
| `pipelines/image_edit/banana_pro_edit.yaml` | `banana_pro_edit` | 图片编辑 | blt |
| `pipelines/text2video/wan2.2_text2video.yaml` | `wan2.2_text2video` | 文生视频 | comfyui |
| `pipelines/image2video/wan2.2_img2video.yaml` | `wan2.2_img2video` | 图生视频 | comfyui |
| `pipelines/music/ace_step1.5.yaml` | `ace_step1.5` | 音乐生成 | comfyui |
| `pipelines/speech/IndexTTS2.yaml` | `IndexTTS2` | 语音生成 | comfyui |

### 内置工作流 JSON 文件

| 文件 | 用途 |
|------|------|
| `workflow/文生图/qwen_2512.json` | 千问文生图工作流 |
| `workflow/图生图/qwen_2512_with_lora.json` | 千问图生图工作流 |
| `workflow/图片编辑/qwen_edit_2511.json` | 千问图片编辑工作流 |
| `workflow/文生视频/wan2.2_text2video.json` | Wan 2.2 文生视频工作流 |
| `workflow/图生视频/wan2.2_image2video.json` | Wan 2.2 图生视频工作流 |
| `workflow/音乐生成/ace_step1.5.json` | ACE Step 1.5 音乐生成工作流 |
| `workflow/语音生成/IndexTTS2.json` | IndexTTS2 语音合成工作流 |
