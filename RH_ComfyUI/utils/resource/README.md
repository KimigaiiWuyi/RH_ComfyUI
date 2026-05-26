# utils/resource — 资源路径管理

## 模块概述

`utils/resource` 负责管理 RH_ComfyUI 插件的所有运行时资源路径，包括配置文件、工作流 JSON、Pipeline YAML 定义和输出目录。同时提供工作流加载和目录初始化功能。

## 文件结构

```
resource/
├── RESOURCE_PATH.py     # 路径常量定义 + 目录初始化
├── pipelines/           # 内置 Pipeline YAML 定义（随插件发布）
│   ├── text2image/      # 文生图 Pipeline
│   ├── image2image/     # 图生图 Pipeline
│   ├── image_edit/      # 图片编辑 Pipeline
│   ├── text2video/      # 文生视频 Pipeline
│   ├── image2video/     # 图生视频 Pipeline
│   ├── music/           # 音乐生成 Pipeline
│   └── speech/          # 语音生成 Pipeline
└── workflow/            # 内置 ComfyUI 工作流 JSON（随插件发布）
    ├── 文生图/
    ├── 图生图/
    ├── 图片编辑/
    ├── 文生视频/
    ├── 图生视频/
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
| `DRAW_TEXT_WORKFLOW_PATH` | `workflow/文生图/` | 文生图工作流 |
| `DRAW_IMAGE_WORKFLOW_PATH` | `workflow/图生图/` | 图生图工作流 |
| `EDIT_WORKFLOW_PATH` | `workflow/图片编辑/` | 图片编辑工作流 |
| `VIDEO_BY_TEXT_WORKFLOW_PATH` | `workflow/文生视频/` | 文生视频工作流 |
| `VIDEO_BY_IMAGE_WORKFLOW_PATH` | `workflow/图生视频/` | 图生视频工作流 |
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

每个 YAML 文件定义一个 Pipeline，示例（[`qwen_2512.yaml`](RH_ComfyUI/utils/resource/pipelines/text2image/qwen_2512.yaml:1)）：

```yaml
name: "qwen_2512"
display_name: "千问 Qwen-Image 2512"
task_type: text2image
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
| `text2image/qwen_2512.yaml` | `qwen_2512` | 文生图 | comfyui | 声明式 |
| `text2image/banana2.yaml` | `banana2` | 文生图 | blt | 编程式 |
| `text2image/banana_pro.yaml` | `banana_pro` | 文生图 | blt | 编程式 |
| `text2image/rh_app_demo.yaml` | `anima` | 文生图 | rh_app | 声明式 |
| `image2image/qwen_2512_img2img.yaml` | `qwen_2512_img2img` | 图生图 | comfyui | 编程式 |
| `image_edit/qwen_2511_edit.yaml` | `qwen_2511_edit` | 图片编辑 | comfyui | 编程式 |
| `image_edit/banana2_edit.yaml` | `banana2_edit` | 图片编辑 | blt | 编程式 |
| `image_edit/banana_pro_edit.yaml` | `banana_pro_edit` | 图片编辑 | blt | 编程式 |
| `text2video/wan2.2_text2video.yaml` | `wan2.2_text2video` | 文生视频 | comfyui | 编程式 |
| `image2video/wan2.2_img2video.yaml` | `wan2.2_img2video` | 图生视频 | comfyui | 编程式 |
| `music/ace_step1.5.yaml` | `ace_step1.5` | 音乐生成 | comfyui | 编程式 |
| `speech/IndexTTS2.yaml` | `IndexTTS2` | 语音生成 | comfyui | 编程式 |

## 用户扩展

用户可在 `data/RHComfyUI/pipelines/` 目录下添加自定义 YAML 文件来扩展 Pipeline，系统启动时会自动加载。也可在 `data/RHComfyUI/workflow/` 下添加对应的工作流 JSON 文件。
