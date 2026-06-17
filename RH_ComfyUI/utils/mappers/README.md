# utils/mappers — 编程式参数映射函数

## 模块概述

`utils/mappers` 包含所有**编程式参数映射函数**。当 Pipeline YAML 中 `mode: programmatic` 时，系统会动态导入并调用此处的映射函数，将 `GenerationRequest` 转换为各后端所需的参数格式。

## 文件结构

```
mappers/
├── __init__.py              # 统一导出所有映射函数
├── gpt_image2.py            # GPT-Image2 / OpenAI 兼容图片映射（banana2、banana_pro、gpt_image2:文生图与图片编辑自动切换）
├── image_edit.py            # ComfyUI 图片编辑映射（qwen_edit,仅接参考图）
├── video.py                 # ComfyUI 视频映射（wan_videogen,0/1/N 张图自动切换）
├── seedance.py              # Seedance 视频映射（多模态/文生/图生/首尾帧自动分发）
├── music.py                 # ComfyUI 音乐映射（ace_step）
└── speech.py                # ComfyUI 语音映射（index_tts2）
```

## 映射函数签名

### ComfyUI 后端映射函数

ComfyUI 后端的映射函数接收 `(request, workflow, api)` 三个参数，返回修改后的 `workflow` 字典：

```python
async def xxx_mapper(
    request: GenerationRequest,  # 统一请求
    workflow: dict,              # 工作流 JSON（已加载）
    api: ComfyUIAPI,             # ComfyUI API 客户端（可用于上传图片）
) -> dict:                       # 返回修改后的工作流
```

### GPT-Image2 / OpenAI 兼容后端映射函数

GPT-Image2 后端的映射函数接收 `(request, api)` 两个参数，直接返回 `NodeOutput` / `PIL.Image`：

```python
async def xxx_mapper(
    request: GenerationRequest,  # 统一请求
    api: GPTImage2API,           # GPT-Image2 API 客户端
) -> NodeOutput:                 # 返回生成结果
```

## 各映射函数详解

### [`gpt_image2.py`](RH_ComfyUI/utils/mappers/gpt_image2.py:1) — GPT-Image2 图片生成（文生图/编辑统一）

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`gpt_image2_mapper()`](RH_ComfyUI/utils/mappers/gpt_image2.py) | `gpt_image2` / `banana2` / `banana_pro` | OpenAI 兼容接口自适应生图/编辑（按 `request.images` 自动切换） |

按 `request.images` 自动分支：
- `0 张图` → 文生图，自动计算宽高比（`_calculate_aspect_ratio()`，支持 21:9、16:9、4:3、1:1、3:4、9:16）
- `1+ 张图` → 图片编辑，参考图随 image_list 传入

Pipeline YAML 通过 `backend_model` 字段声明厂商模型 ID（如 `gemini-3.1-flash-image-preview`、`nano-banana-2-2k`），Adapter 在执行前注入到 `request.params["model"]`。

### [`image_edit.py`](RH_ComfyUI/utils/mappers/image_edit.py:1) — ComfyUI 图片编辑

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`qwen_edit_mapper()`](RH_ComfyUI/utils/mappers/image_edit.py:9) | `qwen_2511` | 千问编辑工作流（仅接受参考图，0 张不可用） |

将 prompt 写入节点 `103`，上传图片到节点 `41/79/81`，支持最多3张参考图。

### [`video.py`](RH_ComfyUI/utils/mappers/video.py:1) — ComfyUI 视频生成（统一 videogen）

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`wan_videogen_mapper()`](RH_ComfyUI/utils/mappers/video.py) | `wan2.2_videogen` | Wan 2.2 统一视频生成：0 图→文生 / 1+ 图→图生 |

按 `request.images` 长度选择工作流：
- `0` → `wan2.2_t2v.json`（prompt 37, width 44, height 34, duration 33）
- `1+` → `wan2.2_i2v.json`（prompt 102, width 289, height 290, duration 294, image 67）

`interpolate_prompt_refs()` 会将 prompt 中的 "图片1/图片2/..." 替换为 "首帧图/尾帧图/参考图N" 等位置标签（仅在 ComfyUI Wan 端生效；Seedance 多模态用 `video_refs`/`audio_refs`/`ordered_content`）。

### [`music.py`](RH_ComfyUI/utils/mappers/music.py:1) — ComfyUI 音乐生成

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`ace_step_mapper()`](RH_ComfyUI/utils/mappers/music.py:9) | `ace_step1.5` | ACE Step 1.5 音乐生成 |

prompt 作为风格描述（节点131），negative_prompt 作为歌词（节点130）。

### [`speech.py`](RH_ComfyUI/utils/mappers/speech.py:1) — ComfyUI 语音生成

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`index_tts2_mapper()`](RH_ComfyUI/utils/mappers/speech.py:9) | `IndexTTS2` | IndexTTS2 语音合成 |

将 prompt（要转换的文字）写入节点 `14`。

## 声明式 vs 编程式映射

| 特性 | 声明式（declarative） | 编程式（programmatic） |
|------|---------------------|---------------------|
| 配置位置 | YAML 的 `mappings` 字段 | Python 函数 |
| 适用场景 | 简单的字段映射 | 复杂逻辑（条件、循环、API 调用） |
| YAML 示例 | `mode: declarative` | `mode: programmatic` |
| 映射函数 | 无需 | `mapper: "module.path:func_name"` |
| 典型用例 | 文生图（prompt→node, width→node） | 图片编辑（上传+多图拼接） |
