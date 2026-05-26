# utils/backends — 后端抽象层

## 模块概述

`utils/backends` 是 RH_ComfyUI 插件的**后端抽象层**，定义了所有 AIGC 后端必须实现的统一接口（`Backend` 基类），并提供了三个具体后端实现：ComfyUI（WebSocket API）、BLT（OpenAI 兼容 API）、RunningHub 原生 AI 应用。

## 文件结构

```
backends/
├── __init__.py          # BackendRegistry 注册表 + init_backends()
├── base.py              # Backend 抽象基类
├── comfyui/             # ComfyUI 后端（WebSocket API）
│   ├── __init__.py
│   ├── api.py           # ComfyUIAPI WebSocket/HTTP 客户端
│   └── executor.py      # ComfyUIBackend 执行器
├── blt/                 # BLT 后端（OpenAI 兼容 API）
│   ├── __init__.py
│   ├── api.py           # BLTAPI HTTP 客户端
│   └── executor.py      # BLTBackend 执行器
└── rh_app/              # RunningHub 原生 AI 应用后端
    ├── __init__.py
    ├── api.py           # RHAppAPI HTTP 客户端
    └── executor.py      # RHAppBackend 执行器
```

## 核心组件

### 1. Backend 抽象基类 [`base.py`](RH_ComfyUI/utils/backends/base.py:1)

所有后端必须实现的接口：

```python
class Backend(ABC):
    name: str  # 后端唯一标识

    @abstractmethod
    async def check_available(self) -> bool:
        """检查后端是否可用（配置是否完整、连接是否正常）"""

    @abstractmethod
    async def get_unavailable_reason(self) -> str:
        """如果不可用，返回原因描述"""

    @abstractmethod
    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        """执行生成任务"""
```

### 2. BackendRegistry 注册表 [`__init__.py`](RH_ComfyUI/utils/backends/__init__.py:1)

全局单例 [`backend_registry`](RH_ComfyUI/utils/backends/__init__.py:27)，启动时通过 [`init_backends()`](RH_ComfyUI/utils/backends/__init__.py:30) 注册所有后端：

```python
backend_registry.register(ComfyUIBackend())
backend_registry.register(BLTBackend())
backend_registry.register(RHAppBackend())
```

### 3. ComfyUI 后端 [`comfyui/`](RH_ComfyUI/utils/backends/comfyui/)

#### API 客户端 [`api.py`](RH_ComfyUI/utils/backends/comfyui/api.py:1)

[`ComfyUIAPI`](RH_ComfyUI/utils/backends/comfyui/api.py:24) 类封装了 ComfyUI 的 WebSocket + HTTP API：

| 方法 | 说明 |
|------|------|
| `connect()` | 建立 WebSocket 连接 |
| `queue_prompt(prompt)` | 提交工作流到 ComfyUI 队列 |
| `track_progress(prompt, prompt_id)` | 通过 WebSocket 跟踪任务进度 |
| `generate_image_by_prompt(prompt)` | 生成图片并返回 PIL.Image |
| `generate_video_by_prompt(prompt)` | 生成视频并返回 bytes |
| `generate_audio_by_prompt(prompt)` | 生成音频并返回 bytes |
| `upload_image(image_path)` | 上传图片到 ComfyUI 服务器 |
| `upload_mp3(mp3)` | 上传音频到 ComfyUI 服务器 |
| `get_images(prompt_id)` | 从历史记录获取生成的图片 |
| `get_videos(prompt_id)` | 从历史记录获取生成的视频 |

**RunningHub 代理支持：** 当配置中包含 "runninghub" 时，自动切换为 RunningHub 代理模式，进度跟踪改为轮询 `/history` 而非 WebSocket。

#### 执行器 [`executor.py`](RH_ComfyUI/utils/backends/comfyui/executor.py:1)

[`ComfyUIBackend`](RH_ComfyUI/utils/backends/comfyui/executor.py:23) 实现 `Backend` 接口：

```python
class ComfyUIBackend(Backend):
    name = "comfyui"

    async def check_available(self) -> bool:
        # 检查 ComfyUI_BaseURL 是否已配置且不是默认值

    async def execute(self, request, pipeline) -> GenerationResult:
        # 1. 加载工作流 JSON
        # 2. 参数映射（声明式 or 编程式）
        # 3. 调用 API 生成（图片/视频/音频）
```

**声明式映射 [`_apply_declarative_mappings()`](RH_ComfyUI/utils/backends/comfyui/executor.py:93)：**

支持两种 YAML 格式：
- 新格式（列表）：`[{source: "prompt", target: "108.inputs.text"}, ...]`
- 旧格式（字典）：`{prompt: {node_id: "108", input_key: "text"}, ...}`

映射规则支持 `source`、`value`、`default`、`template`、`type`（image/upload_image）等字段。

### 4. BLT 后端 [`blt/`](RH_ComfyUI/utils/backends/blt/)

#### API 客户端 [`api.py`](RH_ComfyUI/utils/backends/blt/api.py:1)

[`BLTAPI`](RH_ComfyUI/utils/backends/blt/api.py:19) 封装了 OpenAI 兼容 API 的图片生成接口：

| 方法 | 说明 |
|------|------|
| `draw_image_by_model(model, prompt)` | 通过 Chat Completions API 生成图片 |
| `draw_image(model, prompt, aspect_ratio)` | 通过 DALL-E 格式 API 生成图片 |

支持自动重试（最多3次）、421 频控等待（180秒）、base64/URL 图片解析。

#### 执行器 [`executor.py`](RH_ComfyUI/utils/backends/blt/executor.py:1)

[`BLTBackend`](RH_ComfyUI/utils/backends/blt/executor.py:15) 实现 `Backend` 接口：

```python
class BLTBackend(Backend):
    name = "blt"

    async def check_available(self) -> bool:
        # 检查 BLT_apikey 是否已配置

    async def execute(self, request, pipeline) -> GenerationResult:
        # BLT 不走工作流，直接调 mapper_func 执行
        result = await pipeline.mapper_func(request, self.api)
```

### 5. RunningHub 原生 AI 应用后端 [`rh_app/`](RH_ComfyUI/utils/backends/rh_app/)

#### API 客户端 [`api.py`](RH_ComfyUI/utils/backends/rh_app/api.py:1)

[`RHAppAPI`](RH_ComfyUI/utils/backends/rh_app/api.py:15) 封装了 RunningHub OpenAPI v2 的 AI 应用接口：

| 方法 | 说明 |
|------|------|
| `get_node_info(webapp_id)` | 获取应用节点信息 |
| `upload_file(file_data)` | 上传文件到 RunningHub |
| `submit_task(webapp_id, node_info_list)` | 提交 AI 应用任务 |
| `query_task(task_id)` | 查询任务状态 |
| `wait_for_result(task_id)` | 轮询等待任务完成 |

#### 执行器 [`executor.py`](RH_ComfyUI/utils/backends/rh_app/executor.py:1)

[`RHAppBackend`](RH_ComfyUI/utils/backends/rh_app/executor.py:22) 实现 `Backend` 接口：

```python
class RHAppBackend(Backend):
    name = "rh_app"

    async def check_available(self) -> bool:
        # 检查 RH_apikey 是否已配置

    async def execute(self, request, pipeline) -> GenerationResult:
        # 1. 从 pipeline.workflow_file 读取 webapp_id
        # 2. 构建 nodeInfoList（声明式映射）
        # 3. 上传图片（如有）
        # 4. 提交任务
        # 5. 轮询等待结果
        # 6. 下载结果文件
```

## 三个后端对比

| 特性 | ComfyUI | BLT | RH App |
|------|---------|-----|--------|
| 后端标识 | `comfyui` | `blt` | `rh_app` |
| 连接方式 | WebSocket + HTTP | HTTP REST | HTTP REST |
| 工作流 | JSON 工作流文件 | 无（直接调 API） | WebApp ID |
| 映射模式 | 声明式 / 编程式 | 仅编程式 | 声明式 |
| 可用性检查 | ComfyUI_BaseURL 配置 | BLT_apikey 配置 | RH_apikey 配置 |
| 支持任务 | 全部7种 | 仅图片生成 | 全部（取决于应用） |
| 代理支持 | RunningHub 代理 | 无 | 原生 |
