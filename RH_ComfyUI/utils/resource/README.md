# utils/resource — 资源路径管理

## 模块概述

`utils/resource` 负责管理 RH_ComfyUI 插件的所有运行时资源路径，包括配置文件、工作流 JSON、Pipeline YAML 定义和输出目录。同时提供工作流加载和目录初始化功能。

## 文件结构

```
resource/
├── RESOURCE_PATH.py     # 路径常量定义 + 目录初始化
├── pipelines/           # 内置 Pipeline YAML 定义（随插件发布）
│   ├── imagegen/        # 图片生成 Pipeline(文生图 / 图生图 / 图片编辑 统一)
│   ├── videogen/        # 视频生成 Pipeline(文生 / 图生 / 首尾帧 / 多模态 统一)
│   ├── music/           # 音乐生成 Pipeline
│   └── speech/          # 语音生成 Pipeline
└── workflow/            # 内置 ComfyUI 工作流 JSON（随插件发布）
    ├── 图片生成/
    ├── 视频生成/
    ├── 音乐生成/
    └── 语音生成/
```

## 核心组件

### [`RESOURCE_PATH.py`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:1)

#### 路径常量

| 常量 | 路径 | 说明 |
|------|------|------|
| `MAIN_PATH` | `data/RHComfyUI/` | 插件运行时根目录 |
| `CONFIG_PATH` | `data/RHComfyUI/config.json` | 配置文件路径 |
| `WORKFLOW_PATH` | `data/RHComfyUI/workflow/` | 运行时工作流目录 |
| `OUTPUT_PATH` | `data/RHComfyUI/output/` | 生成输出目录 |
| `PIPELINES_PATH` | `data/RHComfyUI/pipelines/` | 运行时 Pipeline 目录（用户可扩展） |
| `_CP_WORKFLOW_PATH` | `resource/workflow/` | 内置工作流目录（随插件发布） |
| `_CP_PIPELINES_PATH` | `resource/pipelines/` | 内置 Pipeline 目录（随插件发布） |

#### 任务类型子目录

| 常量 | 路径 | 说明 |
|------|------|------|
| `IMAGEGEN_WORKFLOW_PATH` | `workflow/图片生成/` | 图片生成工作流(文生图/图生图/图片编辑统一) |
| `VIDEO_WORKFLOW_PATH` | `workflow/视频生成/` | 视频生成工作流(文生/图生/首尾帧统一) |
| `MUSIC_WORKFLOW_PATH` | `workflow/音乐生成/` | 音乐生成工作流 |
| `SPEECH_WORKFLOW_PATH` | `workflow/语音生成/` | 语音生成工作流 |

#### 工作流加载 [`load_workflow(path)`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:36)

```python
def load_workflow(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        workflow = json.load(f)
    # 自动随机化所有 seed，确保每次生成结果不同
    for i in workflow:
        if workflow[i]["class_type"] == "RandomNoise":
            workflow[i]["inputs"]["noise_seed"] = random.randint(0, 1000000000)
        if "seed" in list(workflow[i]["inputs"].keys()):
            workflow[i]["inputs"]["seed"] = random.randint(0, 1000000000)
    return workflow
```

#### 目录初始化 [`init_dir()`](RH_ComfyUI/utils/resource/RESOURCE_PATH.py:48)

模块导入时自动执行，完成：
1. 创建所有运行时目录
2. 将内置 workflow JSON 复制到 `data/RHComfyUI/workflow/`
3. 将内置 Pipeline YAML 复制到 `data/RHComfyUI/pipelines/`

### Pipeline YAML 定义

每个 YAML 文件定义一个 Pipeline，示例（[`qwen_2512.yaml`](RH_ComfyUI/utils/resource/pipelines/imagegen/qwen_2512.yaml:1)）：

```yaml
name: "qwen_2512"
display_name: "千问 Qwen-Image 2512"
task_type: image
backend: comfyui
point_cost: 2
description: "千问Image2512模型..."
knowledge_content: "详细说明..."
requirements:
  - comfyui_url
workflow: "qwen_2512.json"
mode: declarative
mappings:
  - source: prompt
    target: "108.inputs.text"
  - source: width
    target: "107.inputs.width"
    default: 720
  - source: height
    target: "107.inputs.height"
    default: 1280
```

### 内置 Pipeline 清单

| YAML 文件 | 名称 | 任务类型 | 后端 | 映射模式 |
|-----------|------|---------|------|---------|
| `imagegen/qwen_2512.yaml` | `qwen_2512` | 文生图(仅 0 图) | comfyui | 声明式 |
| `imagegen/banana2.yaml` | `banana2` | 文生图/编辑(0~3 图) | gpt_image2 | 编程式 |
| `imagegen/banana_pro.yaml` | `banana_pro` | 文生图/编辑(0~3 图) | gpt_image2 | 编程式 |
| `imagegen/rh_app_demo.yaml` | `anima` | 文生图(仅 0 图) | rh_app | 声明式 |
| `imagegen/qwen_2511.yaml` | `qwen_2511` | 图片编辑(1~3 图) | comfyui | 编程式 |
| `imagegen/gpt_image2.yaml` | `gpt_image2` | 文生图/编辑(0~N 图) | gpt_image2 | 编程式 |
| `videogen/wan2.2_videogen.yaml` | `wan2.2_videogen` | 视频生成(0/1/N 图) | comfyui | 编程式 |
| `videogen/seedance2.yaml` | `seedance2` | 视频生成(多模态) | seedance | 编程式 |
| `videogen/seedance2_fast.yaml` | `seedance2_fast` | 视频生成(快速版) | seedance | 编程式 |
| `videogen/seedance15_pro.yaml` | `seedance15_pro` | 视频生成(高清+flex) | seedance | 编程式 |
| `music/ace_step1.5.yaml` | `ace_step1.5` | 音乐生成 | comfyui | 编程式 |
| `speech/IndexTTS2.yaml` | `IndexTTS2` | 语音生成 | comfyui | 编程式 |

## 用户扩展

用户可在 `data/RHComfyUI/pipelines/` 目录下添加自定义 YAML 文件来扩展 Pipeline，系统启动时会自动加载。也可在 `data/RHComfyUI/workflow/` 下添加对应的工作流 JSON 文件。
