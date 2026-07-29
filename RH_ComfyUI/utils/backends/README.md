# utils/backends — 后端抽象层

## 模块概述

`utils/backends` 是 RH_ComfyUI 插件的**后端抽象层**，定义了所有 AIGC 后端必须实现的统一接口（`Adapter` 基类），并提供了多个具体后端实现：ComfyUI（WebSocket API）、GPT-Image2 / OpenAI 兼容协议生图、RunningHub 原生 AI 应用、MiniMax、MIMO TTS、Seedance 视频。

## 文件结构

```
backends/
├── __init__.py          # AdapterRegistry 注册表 + init_backends()
├── base.py              # Adapter 抽象基类
├── comfyui/             # ComfyUI 后端（WebSocket API）
│   ├── __init__.py
│   ├── api.py           # ComfyUIAPI WebSocket/HTTP 客户端
│   └── executor.py      # ComfyUIAdapter 执行器
├── gpt_image2/          # GPT-Image2 / OpenAI 兼容协议生图后端
│   ├── __init__.py
│   ├── api.py           # GPTImage2API HTTP 客户端
│   └── executor.py      # GPTImage2Adapter 执行器
├── rh_app/              # RunningHub 原生 AI 应用后端
│   ├── __init__.py
│   ├── api.py           # RHAppAPI HTTP 客户端
│   └── executor.py      # RHAppAdapter 执行器
├── minimax/             # MiniMax 文生图 / 图生图 / T2A 语音后端
├── mimo/                # MiMo TTS 后端
└── seedance/            # Seedance 视频生成后端
```

## 核心组件

### 1. Adapter 抽象基类 [`base.py`](RH_ComfyUI/utils/backends/base.py:1)

所有后端必须实现的接口：

```python
class Adapter(ABC):
    name: str  # 后端唯一标识

    @abstractmethod
    async def check_available(self) -> bool:
        """检查后端是否可用（配置是否完整、连接是否正常）"""

    @abstractmethod
    async def get_unavailable_reason(self) -> str:
        """如果不可用，返回原因描述"""

    @abstractmethod
    def capabilities(self) -> CapabilityManifest:
        """声明该后端能处理哪些任务、消费哪些参数"""

    @abstractmethod
    async def execute(self, request: GenerationRequest, node: NodeDef, *, on_progress=None) -> NodeOutput:
        """执行生成任务"""
```

### 2. AdapterRegistry 注册表 [`__init__.py`](RH_ComfyUI/utils/backends/__init__.py:1)

全局单例 [`backend_registry`](RH_ComfyUI/utils/backends/__init__.py:25)，启动时通过 [`init_backends()`](RH_ComfyUI/utils/backends/__init__.py:40) 注册所有后端：

```python
backend_registry.register(ComfyUIAdapter())
backend_registry.register(GPTImage2Adapter())
backend_registry.register(RHAppAdapter())
backend_registry.register(MiniMaxAdapter())
backend_registry.register(MIMOAdapter())
```

> Seedance 不再是 Adapter：每家供应商(ark/runninghub/网关)= 一个
> `SeedanceProviderChannel`([`seedance/channel.py`](RH_ComfyUI/utils/backends/seedance/channel.py:1)),
> 由通用 `LoadBalancer` 统一排序 / 熔断 / 故障切换。SeedanceProvider 的
> render/parse/poll 机制([`seedance/provider.py`](RH_ComfyUI/utils/backends/seedance/provider.py:1))
> 保留,通道只是薄包装。

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

[`ComfyUIAdapter`](RH_ComfyUI/utils/backends/comfyui/executor.py:23) 实现 `Adapter` 接口：

```python
class ComfyUIAdapter(Adapter):
    name = "comfyui"

    async def check_available(self) -> bool:
        # 检查 ComfyUI_BaseURL 是否已配置

    async def execute(self, request, node, *, on_progress=None) -> NodeOutput:
        # 1. 加载工作流 JSON
        # 2. 参数映射（声明式 or 编程式）
        # 3. 调用 API 生成（图片/视频/音频）
```

**声明式映射 [`_apply_declarative_mappings()`](RH_ComfyUI/utils/backends/comfyui/executor.py:93)：**

支持两种 YAML 格式：
- 新格式（列表）：`[{source: "prompt", target: "108.inputs.text"}, ...]`
- 旧格式（字典）：`{prompt: {node_id: "108", input_key: "text"}, ...}`

映射规则支持 `source`、`value`、`default`、`template`、`type`（image/upload_image）等字段。

### 4. GPT-Image2 后端 [`gpt_image2/`](RH_ComfyUI/utils/backends/gpt_image2/)

#### API 客户端 [`api.py`](RH_ComfyUI/utils/backends/gpt_image2/api.py:1)

[`GPTImage2API`](RH_ComfyUI/utils/backends/gpt_image2/api.py:19) 封装了 OpenAI 兼容 API 的图片生成接口：

| 方法 | 说明 |
|------|------|
| `draw_image_by_model(model, prompt)` | 通过 Chat Completions API 生成图片 |
| `draw_image(model, prompt, aspect_ratio, image_list)` | 通过 DALL-E 格式 API 生成/编辑图片 |

支持自动重试（最多3次）、421 频控等待（180秒）、base64/URL 图片解析。后端 Base URL 可指向任意 OpenAI 兼容服务（OpenAI 官方 / OneAPI / NewAPI / OpenRouter / SiliconFlow / 本地 Ollama 等）。

#### 执行器 [`executor.py`](RH_ComfyUI/utils/backends/gpt_image2/executor.py:1)

[`GPTImage2Adapter`](RH_ComfyUI/utils/backends/gpt_image2/executor.py:24) 实现 `Adapter` 接口：

```python
class GPTImage2Adapter(Adapter):
    name = "gpt_image2"

    async def check_available(self) -> bool:
        # 检查 OpenAI_Image_apikey 是否已配置

    async def execute(self, request, node, *, on_progress=None) -> NodeOutput:
        # 注入 backend_model（节点 YAML 中声明的 vendor model id）
        # 直接调 mapper_func 执行（单端点同时支持文生图 / 图生图 / 编辑）
        result = await node.mapper_func(request, self.api)
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

[`RHAppAdapter`](RH_ComfyUI/utils/backends/rh_app/executor.py:22) 实现 `Adapter` 接口：

```python
class RHAppAdapter(Adapter):
    name = "rh_app"

    async def check_available(self) -> bool:
        # 检查 RH_apikey 是否已配置

    async def execute(self, request, node, *, on_progress=None) -> NodeOutput:
        # 1. 从 node.workflow_file 读取 webapp_id
        # 2. 构建 nodeInfoList（声明式映射）
        # 3. 上传图片（如有）
        # 4. 提交任务
        # 5. 轮询等待结果
        # 6. 下载结果文件
```

## 适配后端对比

| 特性 | ComfyUI | GPT-Image2 | RH App | MiniMax | MIMO | Seedance |
|------|---------|------------|--------|---------|------|----------|
| 后端标识 | `comfyui` | `gpt_image2` | `rh_app` | `minimax` | `mimo` | `seedance` |
| 连接方式 | WebSocket + HTTP | HTTP REST | HTTP REST | HTTP REST | HTTP REST | HTTP REST |
| 工作流 | JSON 工作流文件 | 无（直接调 API） | WebApp ID | 无（直接调 API） | 无（直接调 API） | 无（直接调 API） |
| 映射模式 | 声明式 / 编程式 | 仅编程式 | 声明式 | 仅编程式 | 仅编程式 | 仅编程式 |
| 可用性检查 | ComfyUI_BaseURL | OpenAI_Image_apikey | RH_apikey | MiniMax_apikey | MIMO_apikey | Seedance_apikey_ark/_gateway/_runninghub |
| 支持任务 | 全部 | 图片生成/编辑 | 全部 | 图片/语音 | 语音 | 视频 |
| 代理支持 | RunningHub 代理 | 任意 OpenAI 兼容网关 | 原生 | 原生 | 原生 | 原生 |
