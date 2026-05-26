# utils/mappers — 编程式参数映射函数

## 模块概述

`utils/mappers` 包含所有**编程式参数映射函数**。当 Pipeline YAML 中 `mode: programmatic` 时，系统会动态导入并调用此处的映射函数，将 `GenerationRequest` 转换为各后端所需的参数格式。

## 文件结构

```
mappers/
├── __init__.py              # 统一导出所有映射函数
├── blt_text2image.py        # BLT 文生图映射（banana2、banana_pro）
├── blt_image_edit.py        # BLT 图片编辑映射（banana2_edit、banana_pro_edit）
├── image_edit.py            # ComfyUI 图片编辑映射（qwen_edit）
├── image2image.py           # ComfyUI 图生图映射（qwen_img2img）
├── video.py                 # ComfyUI 视频映射（wan 文生视频/图生视频）
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

### BLT 后端映射函数

BLT 后端的映射函数接收 `(request, api)` 两个参数，直接返回 `GenerationResult` 或 `PIL.Image`：

```python
async def xxx_mapper(
    request: GenerationRequest,  # 统一请求
    api: BLTAPI,                 # BLT API 客户端
) -> GenerationResult:           # 返回生成结果
```

## 各映射函数详解

### [`blt_text2image.py`](RH_ComfyUI/utils/mappers/blt_text2image.py:1) — BLT 文生图

| 函数 | Pipeline | API 模型 | 说明 |
|------|----------|---------|------|
| [`banana2_mapper()`](RH_ComfyUI/utils/mappers/blt_text2image.py:26) | `banana2` | `gemini-3.1-flash-image-preview` | Gemini 3.1 Flash 快速生图 |
| [`banana_pro_mapper()`](RH_ComfyUI/utils/mappers/blt_text2image.py:48) | `banana_pro` | `nano-banana-2-2k` | Banana Pro 高质量生图 |

自动计算宽高比（`_calculate_aspect_ratio()`），支持 21:9、16:9、4:3、1:1、3:4、9:16。

### [`blt_image_edit.py`](RH_ComfyUI/utils/mappers/blt_image_edit.py:1) — BLT 图片编辑

| 函数 | Pipeline | 说明 |
|------|----------|------|
| `banana2_edit_mapper()` | `banana2_edit` | Gemini 3.1 Flash 图片编辑 |
| `banana_pro_edit_mapper()` | `banana_pro_edit` | Banana Pro 图片编辑 |

将参考图片 base64 编码后拼接到 prompt 中，调用 Chat Completions API。

### [`image_edit.py`](RH_ComfyUI/utils/mappers/image_edit.py:1) — ComfyUI 图片编辑

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`qwen_edit_mapper()`](RH_ComfyUI/utils/mappers/image_edit.py:9) | `qwen_2511_edit` | 千问编辑工作流 |

将 prompt 写入节点 `103`，上传图片到节点 `41/79/81`，支持最多3张参考图。

### [`image2image.py`](RH_ComfyUI/utils/mappers/image2image.py:1) — ComfyUI 图生图

| 函数 | Pipeline | 说明 |
|------|----------|------|
| `qwen_img2img_mapper()` | `qwen_2512_img2img` | 千问图生图工作流 |

### [`video.py`](RH_ComfyUI/utils/mappers/video.py:1) — ComfyUI 视频生成

| 函数 | Pipeline | 说明 |
|------|----------|------|
| [`wan_text2video_mapper()`](RH_ComfyUI/utils/mappers/video.py:9) | `wan2.2_text2video` | Wan 2.2 文生视频 |
| [`wan_img2video_mapper()`](RH_ComfyUI/utils/mappers/video.py:22) | `wan2.2_img2video` | Wan 2.2 图生视频 |

设置 prompt（节点37/102）、width（节点44/289）、height（节点34/290）、duration（节点33/294）。

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
