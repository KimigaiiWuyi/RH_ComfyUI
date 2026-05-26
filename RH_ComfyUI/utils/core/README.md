# utils/core — 核心引擎层

## 模块概述

`utils/core` 是 RH_ComfyUI 插件的**核心引擎层**，是整个系统的心脏。它定义了统一的请求/响应模型、Pipeline 注册表、智能路由器、统一执行器和命令解析器，实现了「声明式 Pipeline 架构」的核心抽象。

## 文件结构

```
core/
├── __init__.py     # 模块导出（统一公开接口）
├── request.py      # 统一请求/响应模型（TaskType、GenerationRequest、GenerationResult）
├── pipeline.py     # Pipeline 注册表（从 YAML 自动加载工作流定义）
├── router.py       # 智能路由器（将请求路由到最合适的 Pipeline）
├── executor.py     # 统一执行器（根据 Pipeline 指定的后端分发执行）
└── parser.py       # 命令解析器（从用户输入中提取模型名和 prompt）
```

## 核心组件

### 1. 请求模型 [`request.py`](RH_ComfyUI/utils/core/request.py:1)

#### 任务类型 [`TaskType`](RH_ComfyUI/utils/core/request.py:10)

```python
class TaskType(str, Enum):
    TEXT2IMAGE = "text2image"    # 文生图
    IMAGE2IMAGE = "image2image"  # 图生图
    IMAGE_EDIT = "image_edit"    # 图片编辑
    TEXT2VIDEO = "text2video"    # 文生视频
    IMAGE2VIDEO = "image2video"  # 图生视频
    MUSIC = "music"              # 音乐生成
    SPEECH = "speech"            # 语音生成
```

#### 输出类型 [`OutputType`](RH_ComfyUI/utils/core/request.py:22)

```python
class OutputType(str, Enum):
    IMAGE = "image"   # 图片输出
    VIDEO = "video"   # 视频输出
    AUDIO = "audio"   # 音频输出
```

#### 统一请求 [`GenerationRequest`](RH_ComfyUI/utils/core/request.py:64)

覆盖所有 AIGC 任务类型的统一请求模型：

| 字段 | 类型 | 说明 | 适用任务 |
|------|------|------|---------|
| `task_type` | `TaskType` | 任务类型（必填） | 全部 |
| `prompt` | `str` | 生成描述（必填） | 全部 |
| `images` | `list[bytes]` | 参考图片列表 | 图生图、编辑、图生视频 |
| `reference_audio` | `Optional[bytes]` | 参考音频 | 语音克隆 |
| `width` | `int` | 输出宽度（默认720） | 图片、视频 |
| `height` | `int` | 输出高度（默认1280） | 图片、视频 |
| `duration` | `int` | 视频时长秒数（默认5） | 视频 |
| `negative_prompt` | `str` | 负面提示词 | 图片、视频 |
| `model` | `Optional[str]` | 显式指定 Pipeline 名 | 全部 |
| `extra` | `dict` | 扩展参数 | 全部 |

便捷属性：`output_type`、`mime_type`、`display_name` 自动从 `task_type` 推断。

#### 统一结果 [`GenerationResult`](RH_ComfyUI/utils/core/request.py:123)

```python
@dataclass
class GenerationResult:
    output_type: OutputType   # 输出类型
    data: bytes               # 生成的数据（图片/视频/音频字节）
    mime_type: str            # MIME 类型
    model_used: str           # 使用的模型显示名
    pipeline_used: str        # 使用的 Pipeline 名
    cost_points: int          # 消耗的积分
    metadata: dict            # 扩展元数据
```

### 2. Pipeline 注册表 [`pipeline.py`](RH_ComfyUI/utils/core/pipeline.py:1)

#### Pipeline 定义 [`PipelineDef`](RH_ComfyUI/utils/core/pipeline.py:17)

```python
@dataclass
class PipelineDef:
    name: str                    # Pipeline 唯一名称
    display_name: str            # 显示名称
    task_type: TaskType          # 任务类型
    backend: str                 # 后端标识（"comfyui" | "blt" | "rh_app"）
    point_cost: int              # 积分消耗
    description: str             # 简短描述
    knowledge_content: str       # 详细说明（AI 知识库用）
    requirements: list[str]      # 依赖配置项
    workflow_file: Optional[str] # 工作流文件名/应用ID
    mode: str                    # 映射模式（"declarative" | "programmatic"）
    mappings: dict               # 声明式映射规则
    mapper_func: Optional[Callable]  # 编程式映射函数
    yaml_path: Path              # YAML 文件路径
```

#### 注册表 [`PipelineRegistry`](RH_ComfyUI/utils/core/pipeline.py:41)

全局单例 [`pipeline_registry`](RH_ComfyUI/utils/core/pipeline.py:132)，启动时从 YAML 文件自动构建：

| 方法 | 说明 |
|------|------|
| `load_from_directory(path)` | 递归扫描目录下所有 `.yaml` 文件 |
| `register(pipeline)` | 注册一个 Pipeline |
| `get(name)` | 按名称精确查找 |
| `get_by_task(task_type)` | 按任务类型查找所有 Pipeline |
| `find_by_partial_name(partial, task_type)` | 模糊匹配（精确 > 前缀 > 包含） |
| `all_pipelines()` | 返回所有已注册 Pipeline |

YAML 加载支持两种映射模式：
- **声明式** (`mode: declarative`)：从 YAML 的 `mappings` 字段读取映射规则
- **编程式** (`mode: programmatic`)：动态导入 `mapper` 字段指定的 Python 函数

### 3. 智能路由器 [`router.py`](RH_ComfyUI/utils/core/router.py:1)

[`route(request)`](RH_ComfyUI/utils/core/router.py:35) 函数是系统的智能路由核心：

**路由策略（按优先级）：**

```
1. 用户显式指定 model → 直接选（精确匹配 → 模糊匹配）
2. 可用性过滤 → 检查后端是否可用（check_available）
3. AI Agent 推荐 → 使用 AI 从可用模型中选择最合适的
4. 优先级兜底 → 按 PRIORITY 配置选择
5. 随机选择 → 所有可用模型中随机选一个
```

**优先级配置 [`PRIORITY`](RH_ComfyUI/utils/core/router.py:24)：**

```python
PRIORITY = {
    TaskType.TEXT2IMAGE: ["qwen_2512", "banana2", "banana_pro", "anima"],
    TaskType.IMAGE2IMAGE: ["qwen_2512_img2img"],
    TaskType.IMAGE_EDIT: ["qwen_2511_edit", "banana2_edit", "banana_pro_edit"],
    TaskType.TEXT2VIDEO: ["wan2.2_text2video"],
    TaskType.IMAGE2VIDEO: ["wan2.2_img2video"],
    TaskType.MUSIC: ["ace_step1.5"],
    TaskType.SPEECH: ["IndexTTS2"],
}
```

**AI 推荐 [`_ai_recommend()`](RH_ComfyUI/utils/core/router.py:136)：**

当有多个可用模型时，使用 GsCore 的 `create_agent` 创建临时 AI Agent，根据用户 prompt 和模型描述推荐最合适的模型。

**异常 [`ModelUnavailableError`](RH_ComfyUI/utils/core/router.py:14)：**

当某个任务类型的所有 Pipeline 都不可用时抛出。

### 4. 统一执行器 [`executor.py`](RH_ComfyUI/utils/core/executor.py:1)

[`execute_generation(request, pipeline)`](RH_ComfyUI/utils/core/executor.py:30) 是整个系统的**唯一执行路径**：

```python
async def execute_generation(request, pipeline) -> GenerationResult:
    backend = backend_registry.get(pipeline.backend)  # 获取后端
    sem = _get_semaphore()                             # 获取并发信号量
    async with sem:                                    # 受限流控制
        result = await backend.execute(request, pipeline)  # 分发执行
        result.pipeline_used = pipeline.name
        result.model_used = pipeline.display_name
        result.cost_points = pipeline.point_cost
        return result
```

**并发控制：** 使用全局 `asyncio.Semaphore`，并发数由配置 `Max_Concurrency` 控制，所有后端共享同一限制。

### 5. 命令解析器 [`parser.py`](RH_ComfyUI/utils/core/parser.py:1)

[`parse_model_from_prompt(text, task_type)`](RH_ComfyUI/utils/core/parser.py:31) 从用户输入中提取可选模型名和实际 prompt：

```python
# 输入: "qwen 一只可爱的猫咪"
# 输出: ("qwen_2512", "一只可爱的猫咪")

# 输入: "一只可爱的猫咪"  （无模型名）
# 输出: (None, "一只可爱的猫咪")
```

解析规则：提取第一个词 → 精确匹配 → 前缀匹配 → 包含匹配 → 不匹配则整个文本作为 prompt。

## 数据流

```
GenerationRequest
    │
    ▼
parser.parse_model_from_prompt() → model_name
    │
    ▼
router.route(request) → PipelineDef
    │
    ▼
executor.execute_generation(request, pipeline) → GenerationResult
    │
    ▼
backend.execute(request, pipeline) → GenerationResult
```
