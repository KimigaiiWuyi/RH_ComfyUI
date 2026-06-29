# RH_ComfyUI 模块参考手册

> **对应代码版本**：v2.x（声明式 Pipeline + Adapter 后端抽象 + Seedance 多供应商 + rh_models Web API）
>
> 本文档以现行代码为准。旧版「7 种 TaskType」「BLT 后端」「utils/ai_tools 单独文件」等描述已不再适用。

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

**职责：** 注册插件、定义启动钩子、挂载 rh_models FastAPI 路由、确保 SERVICE/PLUGIN 配置注册到 Web 控制台。

```python
# 插件注册
Plugins(name="RH_ComfyUI", force_prefix=["rh", "cf", "RH"], allow_empty_prefix=False)

# 触发 rh_models setup():挂载 /RH_ComfyUI/models 系列 FastAPI 路由 + 注册命令
from . import rh_models

# 确保配置在初始化时被注册到 gsuid_core 网页控制台
from .rh_config.comfyui_config import PLUGIN_CONFIG, SERVICE_CONFIG


# 启动钩子
@on_core_start
async def init_pipeline_system() -> None:
    registry = init_backends()                                # 注册后端
    if _CP_PIPELINES_PATH.exists():
        pipeline_registry.load_from_directory(_CP_PIPELINES_PATH)  # 加载内置 Pipeline
    pipeline_registry.load_from_directory(PIPELINES_PATH)     # 加载运行时 Pipeline
    register_pipeline_knowledge()                             # 注册 AI 知识库
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
- [`__init__.py`](RH_ComfyUI/rh_generate/__init__.py:1) — 命令处理器 + `_do_generate()` 通用流程（含积分自动退还 + 进度回调）
- [`_knowledge.py`](RH_ComfyUI/rh_generate/_knowledge.py:1) — AI 知识库注册

**关键函数：**

| 函数 | 行号 | 说明 |
|------|------|------|
| `_do_generate()` | [57](RH_ComfyUI/rh_generate/__init__.py:57) | 通用生成执行流程：路由→积分→执行→失败自动退积分 |
| `generate_image()` | [131](RH_ComfyUI/rh_generate/__init__.py:131) | 生图命令处理器（0 图=文生图，1+ 图=图片编辑） |
| `edit_image()` | [181](RH_ComfyUI/rh_generate/__init__.py:181) | 改图命令处理器（显式 IMAGE 模态 + images 必填） |
| `generate_video()` | [238](RH_ComfyUI/rh_generate/__init__.py:238) | 生视频命令处理器（自动适配 T2V/I2V/FLF/MMV） |
| `generate_music()` | [301](RH_ComfyUI/rh_generate/__init__.py:301) | 生音乐命令处理器 |
| `generate_speech()` | [362](RH_ComfyUI/rh_generate/__init__.py:362) | 生语音命令处理器（支持情绪 `[xxx]` 标签 + 参考音频） |
| `list_models()` | [411](RH_ComfyUI/rh_generate/__init__.py:411) | 模型列表命令（@on_fullmatch） |
| `model_detail()` | [438](RH_ComfyUI/rh_generate/__init__.py:438) | 模型详情命令 |
| `register_pipeline_knowledge()` | [11](RH_ComfyUI/rh_generate/_knowledge.py:11) | 将 Pipeline 注册为 AI 知识库 |

> **说明**：`list_models` 和 `model_detail` 也存在于 `rh_models/__init__.py`（FastAPI + 命令触发器双入口），二者功能互补：`rh_generate.list_models` 在 SV 内仅显示可用列表，`rh_models.cmd_list_models` 支持按任务类型过滤。

### [`rh_admin/`](RH_ComfyUI/rh_admin/) — 积分管理模块

**文件：**
- [`__init__.py`](RH_ComfyUI/rh_admin/__init__.py:1) — 命令处理器（增加/减少/查询积分）
- [`commands.py`](RH_ComfyUI/rh_admin/commands.py:1) — 核心逻辑 + AI Tools

**关键函数：**

| 函数 | 文件:行号 | 说明 |
|------|----------|------|
| `add_points()` | `__init__.py:29` | 管理员增加积分命令处理器 |
| `deduct_points()` | `__init__.py:53` | 管理员减少积分命令处理器 |
| `query_points()` | `__init__.py:78` | 查询积分命令处理器（普通用户只能查自己） |
| `parse_add_points_args()` | `commands.py:22` | 解析增减积分参数 |
| `parse_query_points_args()` | `commands.py:60` | 解析查询积分参数 |
| `check_pm()` | `commands.py:92` | 管理员权限校验（@ai_tools check_func） |
| `add_user_points()` | `commands.py:107` | 增加积分（@ai_tools，需管理员权限） |
| `deduct_user_points()` | `commands.py:141` | 扣除积分（@ai_tools，需管理员权限；积分不足时扣全部剩余） |
| `query_user_points()` | `commands.py:184` | 查询积分（@ai_tools） |

### [`rh_agent/`](RH_ComfyUI/rh_agent/) — AI 代理注册模块

**文件：** [`__init__.py`](RH_ComfyUI/rh_agent/__init__.py:1)

**关键组件：**

| 组件 | 行号 | 说明 |
|------|------|------|
| `RH_AIGC_AGENT_PROMPT` | [16](RH_ComfyUI/rh_agent/__init__.py:16) | AIGC 创作代理系统提示词 |
| `register_rh_aigc_agent()` | [61](RH_ComfyUI/rh_agent/__init__.py:61) | 注册能力代理画像（含工具白名单 + 关键词） |
| 模块加载时自动执行 | [121](RH_ComfyUI/rh_agent/__init__.py:121) | 立即调用 `register_rh_aigc_agent()` |

### [`rh_models/`](RH_ComfyUI/rh_models/) — 模型清单模块（命令 + Web API + AI 工具）

**文件：**
- [`__init__.py`](RH_ComfyUI/rh_models/__init__.py:1) — 命令触发器（`模型列表`/`模型清单`/`可用模型`）
- [`api.py`](RH_ComfyUI/rh_models/api.py:1) — 模型目录聚合逻辑（`build_model_catalog` + `ModelEntry`）
- [`utils.py`](RH_ComfyUI/rh_models/utils.py:1) — 文本格式化与 AI 工具 `ai_list_models`
- [`webapi.py`](RH_ComfyUI/rh_models/webapi.py:1) — FastAPI 路由（`/RH_ComfyUI/models[/...]`）

**关键组件：**

| 组件 | 行号 | 说明 |
|------|------|------|
| `ModelEntry` | `api.py:60` | 单个模型对外暴露视图（dataclass） |
| `build_model_catalog()` | `api.py:187` | 构建完整模型清单（支持 include_unavailable/task_type 过滤） |
| `get_models_by_task()` | `api.py:263` | 按任务类型过滤（FastAPI 路由使用） |
| `build_backend_summary()` | `api.py:274` | 后端可用性摘要 |
| `format_text()` | `utils.py:23` | 把目录数据格式化为人类可读文本 |
| `ai_list_models()` | `utils.py:73` | AI 工具：返回 LLM 友好的纯文本模型清单 |
| `cmd_list_models` | `__init__.py:29` | 命令触发器（支持中英文别名过滤） |
| `GET /RH_ComfyUI/models` | `webapi.py:31` | 全量模型清单 |
| `GET /RH_ComfyUI/models/{task_type}` | `webapi.py:63` | 按任务类型过滤 |
| `GET /RH_ComfyUI/models/summary` | `webapi.py:73` | 后端可用性摘要 |

### [`rh_config/`](RH_ComfyUI/rh_config/) — 配置管理模块

**文件：**
- [`comfyui_config.py`](RH_ComfyUI/rh_config/comfyui_config.py:1) — `SERVICE_CONFIG` / `PLUGIN_CONFIG` 两个 `StringConfig` 实例
- [`service_config.py`](RH_ComfyUI/rh_config/service_config.py:1) — 上游服务连接信息（API Key / BaseURL / Seedance 供应商配置）
- [`plugin_config.py`](RH_ComfyUI/rh_config/plugin_config.py:1) — 插件自身行为（并发上限 / 积分规则）
- [`__init__.py`](RH_ComfyUI/rh_config/__init__.py:1) — 占位（无运行时逻辑）

**配置拆分（取代旧的 `RHCOMFYUI_CONFIG`）：**
- `SERVICE_CONFIG`：所有上游服务的 API Key / BaseURL，按 `GsDivider` 分组（ComfyUI / RunningHub / MiniMax / MiMo / Seedance 三供应商 / Seedance 负载均衡 / OpenAI 兼容生图）
- `PLUGIN_CONFIG`：并发上限、初始积分、各业务积分消耗

**配置项数量：** `SERVICE_CONFIG_DEFAULT` 含 ~18 个有效配置 + 7 个 `GsDivider`；`PLUGIN_CONFIG_DEFAULT` 含 6 个有效配置 + 3 个 `GsDivider`（详见 [`service_config.py`](RH_ComfyUI/rh_config/service_config.py:1) / [`plugin_config.py`](RH_ComfyUI/rh_config/plugin_config.py:1)）。

### [`rh_help/`](RH_ComfyUI/rh_help/) — 帮助模块

**文件：** [`__init__.py`](RH_ComfyUI/rh_help/__init__.py:1)

注册 `rh 帮助` 触发器，同时通过 `register_help("RH_ComfyUI", ...)` 挂入全局帮助一览。

---

## 三、utils/core — 核心引擎

### [`types.py`](RH_ComfyUI/utils/core/types.py:1) — 核心类型系统（新增）

| 组件 | 行号 | 说明 |
|------|------|------|
| `PortType` | [24](RH_ComfyUI/utils/core/types.py:24) | 端口类型枚举（STRING/TEXT/INTEGER/NUMBER/BOOLEAN/ENUM/LIST/IMAGE/VIDEO/AUDIO/CONTENT/OUTPUT_*） |
| `PortSpec` | [64](RH_ComfyUI/utils/core/types.py:64) | 端口规格（type/required/default/minimum/maximum/values/item_type/min_items/max_items/mime_types） |
| `MediaKind` | [157](RH_ComfyUI/utils/core/types.py:157) | 媒体类型枚举（IMAGE/VIDEO/AUDIO） |
| `MediaRef` | [201](RH_ComfyUI/utils/core/types.py:201) | 媒体引用（data 或 url，可嗅探 mime_type） |
| `ContentItemType` | [270](RH_ComfyUI/utils/core/types.py:270) | 有序内容项类型枚举（TEXT/IMAGE/VIDEO/AUDIO/DRAFT_TASK） |
| `ContentItem` | [289](RH_ComfyUI/utils/core/types.py:289) | 有序多模态内容项（Seedance 风格） |
| `CapabilityManifest` | [409](RH_ComfyUI/utils/core/types.py:409) | Adapter/Node 能力声明（驱动路由） |
| `ProgressEvent` | [440](RH_ComfyUI/utils/core/types.py:440) | 进度上报事件（stage/percent/message） |
| `NodeOutput` | [459](RH_ComfyUI/utils/core/types.py:459) | 统一节点输出（取代旧 GenerationResult 主输出 + 扩展字段） |

便利构造器：`image_ref` / `video_ref` / `audio_ref` / `text_item` / `image_item` / `video_item` / `audio_item` / `draft_task_item`。

### [`request.py`](RH_ComfyUI/utils/core/request.py:1) — 请求/响应模型

| 组件 | 行号 | 说明 |
|------|------|------|
| `TaskType` | [54](RH_ComfyUI/utils/core/request.py:54) | **4 种任务类型枚举**（IMAGE/VIDEO/MUSIC/SPEECH），按"输出模态"分类 |
| `OutputType` | [72](RH_ComfyUI/utils/core/request.py:72) | 3 种输出类型枚举（IMAGE/VIDEO/AUDIO） |
| `TASK_OUTPUT_MAP` | [81](RH_ComfyUI/utils/core/request.py:81) | 任务类型→输出类型映射 |
| `TASK_MIME_MAP` | [89](RH_ComfyUI/utils/core/request.py:89) | 任务类型→MIME类型映射 |
| `TASK_DISPLAY_NAME` | [97](RH_ComfyUI/utils/core/request.py:97) | 任务类型→中文名映射 |
| `normalize_task_type()` | [39](RH_ComfyUI/utils/core/request.py:39) | 旧式细分类型(text2image/image2image 等)→当前 TaskType 归一化 |
| `GenerationRequest` | [110](RH_ComfyUI/utils/core/request.py:110) | 统一请求模型（dataclass），含 prompt/images/video_refs/audio_refs/ordered_content/reference_audio/mood/voice_id/speed/language_boost/duration/ratio/resolution/seed/generate_audio/watermark/camera_fixed/return_last_frame/service_tier/params/model |
| `GenerationResult` | [299](RH_ComfyUI/utils/core/request.py:299) | 统一响应模型（含 outputs/usage/raw 扩展字段，兼容旧 API） |
| `GenerationResult.from_node_output()` | [320](RH_ComfyUI/utils/core/request.py:320) | 从 NodeOutput 转换 |

> **变更提示**：旧文档描述的「7 种任务类型(text2image/image2image/image_edit/text2video/image2video/music/speech)」已被收编为 4 种 TaskType + 输入档案路由。具体形态（首尾帧 / 多模态 / 编辑）由 `NodeDef.inputs` 中的图片数量 `min_items/max_items` 自动决定。

### [`pipeline.py`](RH_ComfyUI/utils/core/pipeline.py:1) — Pipeline / Node 注册表

| 组件 | 行号 | 说明 |
|------|------|------|
| `NodeDef` | [66](RH_ComfyUI/utils/core/pipeline.py:66) | Pipeline 定义数据类（**取代旧 PipelineDef 作为唯一来源**，见下文别名） |
| `PipelineDef` | [121](RH_ComfyUI/utils/core/pipeline.py:121) | `NodeDef` 的向后兼容别名 |
| `PipelineRegistry` | [129](RH_ComfyUI/utils/core/pipeline.py:129) | 注册表类 |
| `PipelineRegistry.load_from_directory()` | [141](RH_ComfyUI/utils/core/pipeline.py:141) | 递归扫描目录下所有 .yaml 文件 |
| `PipelineRegistry._load_yaml()` | [156](RH_ComfyUI/utils/core/pipeline.py:156) | 解析单个 YAML（含 inputs/outputs/capabilities 自动推断） |
| `PipelineRegistry._import_mapper_module()` | [290](RH_ComfyUI/utils/core/pipeline.py:290) | 兼容多种包名前缀的 mapper 模块导入 |
| `PipelineRegistry.register()` | [326](RH_ComfyUI/utils/core/pipeline.py:326) | 注册 Pipeline（按 supported_tasks 分桶） |
| `PipelineRegistry.get()` | [341](RH_ComfyUI/utils/core/pipeline.py:341) | 按名称查找 |
| `PipelineRegistry.get_by_task()` | [344](RH_ComfyUI/utils/core/pipeline.py:344) | 按任务类型查找 |
| `PipelineRegistry.find_by_partial_name()` | [350](RH_ComfyUI/utils/core/pipeline.py:350) | 模糊匹配（精确 > 前缀 > 包含） |
| `PipelineRegistry.all_pipelines()` | [347](RH_ComfyUI/utils/core/pipeline.py:347) | 列出所有 Pipeline |
| `pipeline_registry` | [373](RH_ComfyUI/utils/core/pipeline.py:373) | 全局单例 |

**`NodeDef` 新增字段（相对旧版 PipelineDef）：**
- `backend_model: Optional[str]` — 厂商模型 ID（Seedance 等多供应商场景下 ark 默认值）
- `backend_models: dict[str, str]` — 多供应商模型映射 `{ark: "xxx", gateway: "yyy", runninghub: ""}`
- `provider: Optional[str]` — 节点级供应商覆盖（固定该节点走某家）
- `inputs: dict[str, PortSpec]` — 类型化输入端口（驱动 Router 输入档案匹配）
- `outputs: dict[str, PortSpec]` — 类型化输出端口
- `capabilities: CapabilityManifest` — 能力声明（含 priority/mode/output_mime/supported_params/supported_tasks/fallback）

### [`router.py`](RH_ComfyUI/utils/core/router.py:1) — 智能路由器

| 组件 | 行号 | 说明 |
|------|------|------|
| `ModelUnavailableError` | [32](RH_ComfyUI/utils/core/router.py:32) | 任务类型无可用节点异常 |
| `_node_supports_request()` | [56](RH_ComfyUI/utils/core/router.py:56) | 输入档案匹配（图片数量/音视频参考/必填项） |
| `_is_available()` | [102](RH_ComfyUI/utils/core/router.py:102) | Adapter 可用性探测 |
| `_collect_unavailable_reasons()` | [113](RH_ComfyUI/utils/core/router.py:113) | 聚合所有不可用原因 |
| `route()` | [137](RH_ComfyUI/utils/core/router.py:137) | 主路由函数（用户指定 → 输入档案过滤 → 可用性 → AI 推荐 → 优先级兜底） |
| `_try_agent_recommendation()` | [211](RH_ComfyUI/utils/core/router.py:211) | 延迟导入 gs_agent.recommend_model，失败安全降级 |
| `_ensure_runtime_initialized()` | [241](RH_ComfyUI/utils/core/router.py:241) | 懒加载兜底（热重载 / 启动钩子未执行时补一次） |
| `get_display_name()` | [236](RH_ComfyUI/utils/core/router.py:236) | 获取任务类型中文显示名 |

> **变更提示**：路由器**不再按"文生图/图生图/编辑"细分**任务桶，而是按"输出模态（4 种）"圈桶 + 输入档案过滤；这意味着同一个 IMAGE 模态下，0 张图和 1+ 张图会路由到不同的 NodeDef（依据各自声明的 `inputs.images.min_items`）。

### [`executor.py`](RH_ComfyUI/utils/core/executor.py:1) — 统一执行器

| 组件 | 行号 | 说明 |
|------|------|------|
| `_get_semaphore()` | [34](RH_ComfyUI/utils/core/executor.py:34) | 获取全局并发信号量（懒加载，按 `Max_Concurrency` 初始化） |
| `_save_output()` | [67](RH_ComfyUI/utils/core/executor.py:67) | 将 `NodeOutput` 落盘到 `OUTPUT_PATH/<task_type>/<ts>.<ext>`，附加产物独立落盘 |
| `execute_generation()` | [110](RH_ComfyUI/utils/core/executor.py:110) | 统一执行入口（按 `node.backend` 取 Adapter → 限流 → 执行 → 落盘 → 包装为 `GenerationResult`） |

### [`parser.py`](RH_ComfyUI/utils/core/parser.py:1) — 命令解析器

| 组件 | 行号 | 说明 |
|------|------|------|
| `parse_model_from_prompt()` | [44](RH_ComfyUI/utils/core/parser.py:44) | 解析模型名和 prompt（**只返回 token**，由 router 做输入感知的最终匹配） |
| `_ensure_registry_loaded()` | [18](RH_ComfyUI/utils/core/parser.py:18) | 懒加载兜底 |
| `MINIMAX_EMOTIONS` | [104](RH_ComfyUI/utils/core/parser.py:104) | MiniMax T2A 支持的情绪标签常量 |
| `parse_mood_from_prompt()` | [121](RH_ComfyUI/utils/core/parser.py:121) | 从文本开头解析情绪标签 `[情绪]` / `[情绪:xxx]` / `[mood:xxx]` |

---

## 四、utils/backends — 后端实现

### [`base.py`](RH_ComfyUI/utils/backends/base.py:1) — Adapter 抽象基类

**`Adapter` ABC 关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `name`（类属性） | [39](RH_ComfyUI/utils/backends/base.py:39) | 后端唯一标识 |
| `check_available()` | [42](RH_ComfyUI/utils/backends/base.py:42) | 检查后端是否可用（配置/连接） |
| `get_unavailable_reason()` | [47](RH_ComfyUI/utils/backends/base.py:47) | 返回不可用原因 |
| `capabilities()` | [52](RH_ComfyUI/utils/backends/base.py:52) | 声明能力（驱动 Router） |
| `execute()` | [60](RH_ComfyUI/utils/backends/base.py:60) | 执行生成任务，返回 `NodeOutput`（含 `on_progress` 回调） |
| `execute_legacy()` | [81](RH_ComfyUI/utils/backends/base.py:81) | 向后兼容入口，返回旧 `GenerationResult` |

**`Backend = Adapter`** 作为向后兼容别名保留。

### [`__init__.py`](RH_ComfyUI/utils/backends/__init__.py:1) — 后端注册表

| 组件 | 行号 | 说明 |
|------|------|------|
| `AdapterRegistry` | [10](RH_ComfyUI/utils/backends/__init__.py:10) | 后端 / Adapter 注册表（取代旧 `BackendRegistry`，旧名作为子类保留） |
| `BackendRegistry` | [36](RH_ComfyUI/utils/backends/__init__.py:36) | `AdapterRegistry` 子类（向后兼容） |
| `backend_registry` | [40](RH_ComfyUI/utils/backends/__init__.py:40) | 全局单例 |
| `init_backends()` | [43](RH_ComfyUI/utils/backends/__init__.py:43) | 启动时注册 6 个 Adapter：ComfyUI / RHApp / MiniMax / MIMO / Seedance / GPTImage2 |

### ComfyUI 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`comfyui/api.py`](RH_ComfyUI/utils/backends/comfyui/api.py:1) | `ComfyUIAPI` | WebSocket + HTTP 客户端（含 `set_workflow_override` / `consume_workflow_override` 工作流覆盖机制；RunningHub 代理模式下自动切换为 `/history` 轮询） |
| [`comfyui/executor.py`](RH_ComfyUI/utils/backends/comfyui/executor.py:1) | `ComfyUIAdapter` | ComfyUI 后端执行器（声明式/编程式映射 + 工作流覆盖重映射 + 进度事件透传） |
| [`comfyui/executor.py:373`](RH_ComfyUI/utils/backends/comfyui/executor.py:373) | `ComfyUIBackend` | `ComfyUIAdapter` 的向后兼容别名 |

**ComfyUIAPI 关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `connect()` | `api.py:71` | 建立 WebSocket 连接（含自动重连监听器） |
| `queue_prompt(prompt)` | `api.py:102` | POST `/prompt` 提交工作流 |
| `track_progress(prompt, prompt_id)` | `api.py:403` | WebSocket 监听（RunningHub 代理改为 `/history` 轮询 + `/openapi/v2/query` 兜底） |
| `generate_image_by_prompt(prompt)` | `api.py:279` | 生成图片返回 PIL.Image |
| `generate_video_by_prompt(prompt)` | `api.py:302` | 生成视频返回 bytes |
| `generate_audio_by_prompt(prompt)` | `api.py:254` | 生成音频返回 bytes |
| `upload_image(image_path)` | `api.py:331` | 上传图片到 ComfyUI 服务器 |
| `upload_mp3(mp3)` | `api.py:328` | 上传音频到 ComfyUI 服务器 |
| `set_workflow_override(filename)` | `api.py:50` | mapper 指定本次生成使用的工作流文件（覆盖 `node.workflow_file`） |
| `consume_workflow_override()` | `api.py:62` | adapter 在执行前读取并清空工作流覆盖 |
| `get_history(prompt_id)` | `api.py:89` | `/history/{prompt_id}` 获取任务结果 |
| `_poll_history_until_complete(prompt_id)` | `api.py:430` | RunningHub 代理 `/history` 轮询（连续空响应回退 `/openapi/v2/query`） |

### GPT-Image2 / OpenAI 兼容后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`gpt_image2/api.py`](RH_ComfyUI/utils/backends/gpt_image2/api.py:1) | `GPTImage2API` | OpenAI 兼容 HTTP 客户端；后端 BaseURL 默认 `https://api.openai.com/v1`，可指向任意 OpenAI 兼容服务（OneAPI / NewAPI / OpenRouter / BLT / SiliconFlow / Ollama 等）；配置优先读 `OpenAI_Image_apikey` / `OpenAI_Image_BaseURL`，回退 `GPT_Image2_apikey` / `GPT_Image2_BaseURL` |
| [`gpt_image2/executor.py`](RH_ComfyUI/utils/backends/gpt_image2/executor.py:1) | `GPTImage2Adapter` | 后端 `name = "gpt-image-2"`（**注意带连字符**，与旧 `gpt_image2` 不同），统一走 `mapper_func`，支持 `NodeOutput` / `GenerationResult` / `PIL.Image` / `bytes` 多种返回类型 |

**GPTImage2API 关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `draw_image(model, prompt, aspect_ratio, image_list)` | `api.py:249` | 通过 DALL-E 格式 API（`/v1/images/generations`）生图/编辑；`image_list` 非空时进入图生图/编辑模式 |
| `draw_image_by_model(model, prompt, ...)` | `api.py:187` | 通过 Chat Completions API 生图（部分服务在 chat 中返回图片链接/base64） |

### RH App 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`rh_app/api.py`](RH_ComfyUI/utils/backends/rh_app/api.py:1) | `RHAppAPI` | RunningHub OpenAPI v2 客户端 |
| [`rh_app/executor.py`](RH_ComfyUI/utils/backends/rh_app/executor.py:1) | `RHAppAdapter` | RH App 后端执行器（仅声明式映射；`node.workflow_file` 作为 `webappId`） |

**RHAppAPI 关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `get_node_info(webapp_id)` | `api.py:35` | 获取应用节点信息 |
| `upload_file(file_data)` | `api.py:49` | 上传文件，返回 fileName |
| `submit_task(webapp_id, node_info_list)` | `api.py:76` | 提交 AI 应用任务 |
| `query_task(task_id)` | `api.py:107` | 查询任务状态 |
| `wait_for_result(task_id)` | `api.py:117` | 轮询等待任务完成 |

### MiniMax 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`minimax/api.py`](RH_ComfyUI/utils/backends/minimax/api.py:1) | `MiniMaxAPI` | MiniMax API 客户端（图像生成 + T2A 异步语音合成 + 文件上传/音色克隆） |
| [`minimax/executor.py`](RH_ComfyUI/utils/backends/minimax/executor.py:1) | `MiniMaxAdapter` | MiniMax 后端执行器（`task_type ∈ {image, speech}`，自动适配）；返回类型支持 `NodeOutput` / `GenerationResult` / `List[PIL.Image]` / `Image.Image` / `bytes` |

**MiniMaxAPI 关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `generate_image()` | `api.py:201` | 文生图/图生图（`/v1/image_generation`，支持 `subject_reference`） |
| `upload_file()` | `api.py:301` | 上传文件到 MiniMax |
| `clone_voice()` | `api.py:361` | 音色快速复刻（`/v1/voice_clone`） |
| `create_t2a_async_task()` | `api.py:417` | 创建异步语音合成任务（`/v1/t2a_async_v2`） |
| `query_t2a_async_task()` | `api.py:496` | 查询异步语音合成任务状态 |
| `retrieve_file()` | `api.py:519` | 下载生成的文件（`/v1/files/retrieve`） |
| `generate_speech()` | `api.py:594` | 高级语音合成接口（自动：创建→轮询→下载） |

### MiMo TTS 后端

| 文件 | 关键类 | 说明 |
|------|--------|------|
| [`mimo/api.py`](RH_ComfyUI/utils/backends/mimo/api.py:1) | `MIMOAPI` | XiaoMi MiMo TTS API 客户端（OpenAI 兼容格式 `/v1/chat/completions`） |
| [`mimo/executor.py`](RH_ComfyUI/utils/backends/mimo/executor.py:1) | `MIMOAdapter` | MiMo TTS 后端执行器（仅 `speech` 任务） |

**MIMOAPI 关键方法：**

| 方法 | 行号 | 说明 |
|------|------|------|
| `generate_speech(text, mood, reference_audio, model, ...)` | `api.py:147` | 语音合成（自动选模型：有参考音频→voiceclone，否则→tts） |

**支持的模型：** `mimo-v2.5-tts`（预置音色）、`mimo-v2.5-tts-voicedesign`（音色设计）、`mimo-v2.5-tts-voiceclone`（音色复刻）。

### Seedance 视频后端（多供应商）

Seedance 是当前最复杂的后端，按"供应商无关 Spec → 供应商驱动执行"分层：

| 文件 | 关键组件 | 说明 |
|------|---------|------|
| [`seedance/executor.py`](RH_ComfyUI/utils/backends/seedance/executor.py:1) | `SeedanceAdapter` | Adapter 主入口（resolve 候选供应商 → classify → provider.run → 下载 → NodeOutput） |
| [`seedance/spec.py`](RH_ComfyUI/utils/backends/seedance/spec.py:1) | `VideoTaskShape` / `MediaRole` / `SpecMedia` / `OrderedSegment` / `VideoGenSpec` | 供应商无关的视频生成意图 |
| [`seedance/classify.py`](RH_ComfyUI/utils/backends/seedance/classify.py:1) | `classify_video_spec(request)` | 按输入自动判定任务形态（T2V / I2V / 首尾帧 / 多模态） |
| [`seedance/provider.py`](RH_ComfyUI/utils/backends/seedance/provider.py:1) | `SeedanceProvider` ABC / `NormalizedTask` / `NormalizedStatus` / `SeedanceProviderError` / `normalize_usage()` | Provider 抽象基类 + 用量归一化 |
| [`seedance/registry.py`](RH_ComfyUI/utils/backends/seedance/registry.py:1) | `get_provider()` / `resolve_provider_candidates()` / `record_provider_failure()` / `get_provider_health()` | 负载均衡策略 + 熔断 |
| [`seedance/api.py`](RH_ComfyUI/utils/backends/seedance/api.py:1) | `SeedanceAPI` | 火山方舟 Seedance 2.0 ARK 原生 API 客户端（`/contents/generations/tasks`） |
| [`seedance/_debug.py`](RH_ComfyUI/utils/backends/seedance/_debug.py:1) | `dump_body` / `mask_body` / `mask_headers` | Dry-Run / 日志脱敏工具 |
| [`seedance/providers/__init__.py`](RH_ComfyUI/utils/backends/seedance/providers/__init__.py:1) | `ArkSeedanceProvider` / `GatewaySeedanceProvider` / `RunningHubSeedanceProvider` | 三个供应商驱动 |
| [`seedance/providers/ark.py`](RH_ComfyUI/utils/backends/seedance/providers/ark.py:1) | `ArkSeedanceProvider` | ARK 官方 / 网关双模（自动按 base_url 切换） |
| [`seedance/providers/gateway.py`](RH_ComfyUI/utils/backends/seedance/providers/gateway.py:1) | `GatewaySeedanceProvider` | 聚合网关（`{code,msg,data}` 信封 + Idempotency-Key） |
| [`seedance/providers/runninghub.py`](RH_ComfyUI/utils/backends/seedance/providers/runninghub.py:1) | `RunningHubSeedanceProvider` | RunningHub（`/image-to-video` + `/multimodal-video`，媒体须公网 URL） |

**Seedance 配置（[`service_config.py`](RH_ComfyUI/rh_config/service_config.py:1)）：**
- 每供应商独立：`Seedance_apikey_{ark|gateway|runninghub}` / `Seedance_BaseURL_{ark|gateway|runninghub}` / `Seedance_Enable_{ark|gateway|runninghub}`
- `Seedance_Load_Balance`：`round_robin`（默认）/ `weighted`（官方优先）/ `least_failures`
- `Seedance_Failure_Threshold`：连续失败多少次后熔断（默认 3）
- `Seedance_Dry_Run`：拦截所有 Seedance 出站请求 + 打印

---

## 五、utils/mappers — 参数映射

| 文件 | 映射函数 | 目标后端 | 说明 |
|------|---------|---------|------|
| [`gpt_image2.py`](RH_ComfyUI/utils/mappers/gpt_image2.py:1) | `gpt_image2_mapper` | gpt-image-2 | 自适应（0 图=文生，1+ 图=编辑；返回 `NodeOutput`） |
| [`image_edit.py`](RH_ComfyUI/utils/mappers/image_edit.py:1) | `qwen_edit_mapper` | comfyui | 千问图片编辑（最多 3 张输入） |
| [`image2image.py`](RH_ComfyUI/utils/mappers/image2image.py:1) | `qwen_img2img_mapper` | comfyui | 千问图生图（denoise 控制重绘强度） |
| [`minimax_text2image.py`](RH_ComfyUI/utils/mappers/minimax_text2image.py:1) | `minimax_image01_mapper` | minimax | MiniMax 文生图 |
| [`minimax_image2image.py`](RH_ComfyUI/utils/mappers/minimax_image2image.py:1) | `minimax_image01_img2img_mapper` | minimax | MiniMax 图生图（`subject_reference`） |
| [`video.py`](RH_ComfyUI/utils/mappers/video.py:1) | `wan_videogen_mapper` / `wan_text2video_mapper` / `wan_img2video_mapper` / `interpolate_prompt_refs` | comfyui | Wan 2.2 统一视频 mapper（按图片数选工作流 + prompt 位置插值） |
| [`music.py`](RH_ComfyUI/utils/mappers/music.py:1) | `ace_step_mapper` | comfyui | ACE Step 1.5 音乐 |
| [`speech.py`](RH_ComfyUI/utils/mappers/speech.py:1) | `index_tts2_mapper` | comfyui | IndexTTS2（支持 `mood` + `reference_audio`） |
| [`minimax_speech.py`](RH_ComfyUI/utils/mappers/minimax_speech.py:1) | `minimax_t2a_speech_mapper` | minimax | MiniMax 语音（情绪/语速/音色克隆 + 缓存） |
| [`mimo_speech.py`](RH_ComfyUI/utils/mappers/mimo_speech.py:1) | `mimo_tts_mapper` | mimo | MiMo 语音（情绪/风格/音色复刻/方言） |
| [`seedance.py`](RH_ComfyUI/utils/mappers/seedance.py:1) | `seedance_video_mapper` / `seedance_text2video_mapper` / `seedance_draft_mapper` | seedance | Seedance YAML 兼容入口（实际执行已迁入 `SeedanceAdapter.execute`） |

---

## 六、utils/database — 数据模型

### [`models.py`](RH_ComfyUI/utils/database/models.py:1)

| 组件 | 行号 | 说明 |
|------|------|------|
| `DEFAULT_POINT` | [10](RH_ComfyUI/utils/database/models.py:10) | 默认初始积分（从 `PLUGIN_CONFIG.Default_Point` 读取） |
| `RHBind` | [13](RH_ComfyUI/utils/database/models.py:13) | 积分表模型（继承 `Bind`，`__table_args__ = {"extend_existing": True}`） |
| `RHBind.create_data()` | [18](RH_ComfyUI/utils/database/models.py:18) | 创建用户数据（带 `point` 字段） |
| `RHBind.add_point()` | [47](RH_ComfyUI/utils/database/models.py:47) | 增加积分（返回 0=成功） |
| `RHBind.get_point()` | [72](RH_ComfyUI/utils/database/models.py:72) | 查询积分（无数据返回 0） |
| `RHBind.deduct_point()` | [86](RH_ComfyUI/utils/database/models.py:86) | 扣除积分（不足返回 False） |
| `SsPushAdmin` | [114](RH_ComfyUI/utils/database/models.py:114) | Web 控制台管理页（`AI绘图积分管理`） |

---

## 七、utils/resource — 资源管理

### [`RESOURCE_PATH.py`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:1)

| 组件 | 行号 | 说明 |
|------|------|------|
| `MAIN_PATH` | [12](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:12) | 插件运行时根目录 |
| `SERVICE_CONFIG_PATH` | [16](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:16) | `service_config.json` 路径 |
| `PLUGIN_CONFIG_PATH` | [17](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:17) | `plugin_config.json` 路径 |
| `_CP_WORKFLOW_PATH` | [19](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:19) | 内置工作流目录（打包在插件内） |
| `WORKFLOW_PATH` | [20](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:20) | 运行时工作流目录 |
| `OUTPUT_PATH` | [21](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:21) | 生成产物落盘目录（executor 自动写入） |
| `_CP_PIPELINES_PATH` | [24](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:24) | 内置 Pipeline 目录 |
| `PIPELINES_PATH` | [26](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:26) | 运行时 Pipeline 目录（用户可扩展） |
| `IMAGEGEN_WORKFLOW_PATH` | [30](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:30) | 图片生成工作流根目录（文生/编辑统一） |
| `DRAW_TEXT_WORKFLOW_PATH` / `DRAW_IMAGE_WORKFLOW_PATH` / `EDIT_WORKFLOW_PATH` | [32-34](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:32) | `IMAGEGEN_WORKFLOW_PATH` 的向后兼容别名 |
| `VIDEO_WORKFLOW_PATH` | [41](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:41) | 视频生成工作流根目录（文生/图生统一） |
| `VIDEO_BY_TEXT_WORKFLOW_PATH` / `VIDEO_BY_IMAGE_WORKFLOW_PATH` | [43-44](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:43) | `VIDEO_WORKFLOW_PATH` 的向后兼容别名 |
| `MUSIC_WORKFLOW_PATH` / `SPEECH_WORKFLOW_PATH` | [35-36](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:35) | 音乐/语音工作流目录 |
| `load_workflow(path)` | [47](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:47) | 加载工作流 JSON + 随机化 seed |
| `init_dir()` | [59](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:59) | 初始化目录结构 + 把内置资源复制到运行时 |

> **变更提示**：旧文档中按 `text2image/image2image/image_edit/text2video/image2video/music/speech` 区分的目录结构已合并为 **4 个根目录**：`imagegen`（文生 + 图生 + 编辑统一）/ `videogen`（文生 + 图生 + 多模态统一）/ `music` / `speech`。具体形态由 NodeDef 的 `inputs` 端口决定。

### 内置 Pipeline YAML 文件

| 文件 | Pipeline 名 | 任务类型 | 后端 | 后端模型 / 备注 |
|------|------------|---------|------|------|
| `pipelines/imagegen/qwen_2512.yaml` | `qwen_2512` | image | comfyui | 千问文生图（声明式） |
| `pipelines/imagegen/qwen_2511.yaml` | `qwen_2511` | image | comfyui | 千问图片编辑（编程式） |
| `pipelines/imagegen/anima1.yaml` | `anima` | image | rh_app | 二次元 AI 应用（`workflow: "2059263409362923521"` 作为 webappId） |
| `pipelines/imagegen/banana2.yaml` | `banana2` | image | gpt-image-2 | `backend_model: gemini-3.1-flash-image-preview` |
| `pipelines/imagegen/banana_pro.yaml` | `banana_pro` | image | gpt-image-2 | `backend_model: nano-banana-2-2k` |
| `pipelines/imagegen/gpt_image2.yaml` | `gpt-image-2` | image | gpt-image-2 | 通用 OpenAI 兼容生图（无 `backend_model`，由 `request.params["model"]` 注入） |
| `pipelines/imagegen/minimax_image01.yaml` | `minimax_image01` | image | minimax | MiniMax image-01 文生图 |
| `pipelines/videogen/wan2.2_videogen.yaml` | `wan2.2_videogen` | video | comfyui | Wan 2.2 统一视频生成（mapper 按图片数自动选 t2v/i2v workflow） |
| `pipelines/videogen/seedance2.yaml` | `seedance2` | video | seedance | Seedance 2.0（多供应商；`backend_models: ark/gateway/runninghub`） |
| `pipelines/videogen/seedance2_fast.yaml` | `seedance2_fast` | video | seedance | Seedance 2.0 Fast（仅 480p/720p） |
| `pipelines/videogen/seedance15_pro.yaml` | `seedance15_pro` | video | seedance | Seedance 1.5 Pro（支持 flex 离线推理） |
| `pipelines/music/ace_step1.5.yaml` | `ace_step1.5` | music | comfyui | ACE Step 1.5 |
| `pipelines/speech/IndexTTS2.yaml` | `IndexTTS2` | speech | comfyui | IndexTTS2 本地 TTS |
| `pipelines/speech/minimax_t2a_speech.yaml` | `minimax_t2a_speech` | speech | minimax | MiniMax T2A 异步语音合成 |
| `pipelines/speech/mimo_tts.yaml` | `mimo_tts` | speech | mimo | 小米 MiMo TTS |

### 内置工作流 JSON 文件

| 文件 | 用途 |
|------|------|
| `workflow/图片生成/qwen_2512.json` | 千问文生图工作流 |
| `workflow/图片生成/qwen_2512_with_lora.json` | 千问图生图工作流（保留为历史产物；新 `qwen_2512_img2img` 节点已不再内置） |
| `workflow/图片生成/qwen_edit_2511.json` | 千问图片编辑工作流 |
| `workflow/视频生成/wan2.2_t2v.json` | Wan 2.2 文生视频工作流 |
| `workflow/视频生成/wan2.2_i2v.json` | Wan 2.2 图生视频工作流 |
| `workflow/音乐生成/ace_step1.5.json` | ACE Step 1.5 音乐生成工作流 |
| `workflow/语音生成/IndexTTS2.json` | IndexTTS2 语音合成工作流 |

---

## 附录：其他工具模块

### [`utils/image_process.py`](RH_ComfyUI/utils/image_process.py:1) — 图片预处理

| 函数 | 行号 | 说明 |
|------|------|------|
| `resize_long_edge(data, max_long_edge=800)` | [26](RH_ComfyUI/utils/image_process.py:26) | 等比缩放最长边到阈值（输出 PNG） |
| `correct_orientation(data)` | [66](RH_ComfyUI/utils/image_process.py:66) | 根据 EXIF Orientation 旋转 |
| `build_process_pipeline(*steps)` | [122](RH_ComfyUI/utils/image_process.py:122) | 组合处理管道 |
| `preprocess_for_video(data, max_long_edge=800)` | [153](RH_ComfyUI/utils/image_process.py:153) | 视频生成前的标准预处理（EXIF + 缩放） |

### [`utils/points.py`](RH_ComfyUI/utils/points.py:1) — 积分检查

| 函数 | 行号 | 说明 |
|------|------|------|
| `check_point(ev, point)` | [21](RH_ComfyUI/utils/points.py:21) | 检查积分并自动扣除；返回 `(success, msg)` |

> 注：模块顶部导出 `Draw_Point` / `Edit_Image_Point` / `Music_Point` / `Speech_Point` / `Video_Point` 五个常量（从 `PLUGIN_CONFIG` 读取），便于业务直接引用；但**当前 `_do_generate()` 实际使用 `node.point_cost`**（由 YAML 声明），不再依赖这些模块级常量。