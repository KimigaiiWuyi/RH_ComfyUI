# rh_agent — AIGC 创作能力代理注册模块

## 模块概述

`rh_agent` 是 RH_ComfyUI 插件的 **AI 能力代理注册模块**。该模块在导入时注册 `rh_aigc_agent` 能力代理画像（CapabilityAgentProfile），让 GsCore 的 AI Agent Mesh 在遇到 AIGC 创作任务时，能够自动选择 RH_ComfyUI 的专业能力代理来完成绘图、视频、音乐、语音等生成指令。

## 文件结构

```
rh_agent/
└── __init__.py      # 能力代理画像定义与注册
```

## 核心组件

### 能力代理画像 [`RH_AIGC_AGENT_PROMPT`](RH_ComfyUI/rh_agent/__init__.py:16)

定义了 AIGC 创作代理的系统提示词，包含：

1. **能力边界**：文生图/图生图、图片编辑、文生视频/图生视频、音乐生成、语音合成
2. **可用工具清单**：`generate_image`、`edit_image`、`generate_video`、`generate_music`、`generate_speech` 等
3. **工作流规范**：理解需求 → 拆解任务 → 批量生成规划 → 构建描述 → 调用工具 → 交付结果
4. **Prompt 优化建议**：自动补充细节、指定模型前缀等
5. **注意事项**：积分消耗、等待时间、错误处理
6. **`_DELIVERY_BOUNDARY`**：必须拼接的交付边界约束，防止画像绕过主人格直接给用户发消息

### 注册函数 [`register_rh_aigc_agent()`](RH_ComfyUI/rh_agent/__init__.py:75)

```python
register_capability_agent(
    CapabilityAgentProfile(
        profile_id="rh_aigc_agent",
        display_name="AIGC 创作代理",
        when_to_use="需要进行 AI 绘图、图片编辑、视频生成、音乐创作、语音合成等 AIGC 创作任务",
        system_prompt=RH_AIGC_AGENT_PROMPT,
        match_keywords=[
            "绘图", "画图", "生图", "生成图片", "AI绘画",
            "文生图", "图生图", "图片编辑", "改图",
            "生视频", "生成视频", "文生视频", "图生视频",
            "生音乐", "生成音乐", "生语音", "语音合成", "AIGC",
        ],
        tool_names=[
            "generate_image", "edit_image", "generate_video",
            "generate_music", "generate_speech",
            "pack_to_zip", "move_file", "copy_file",
        ],
        max_iterations=15,
        max_tokens=30000,
    )
)
```

### 匹配关键词

当用户消息包含以下关键词时，AI Agent Mesh 会自动路由到该代理：

| 类别 | 关键词 |
|------|--------|
| 图片生成 | 绘图、画图、生图、画一张、生成图片、AI绘画、AI画图、文生图、图生图 |
| 图片编辑 | 图片编辑、改图、编辑图片、P图、修图 |
| 视频生成 | 生视频、生成视频、视频生成、文生视频、图生视频、让图片动起来 |
| 音乐生成 | 生音乐、生成音乐、音乐创作、背景音乐、配乐 |
| 语音合成 | 生语音、语音合成、文字转语音、TTS |
| 通用 | AIGC、AI创作、AI生成 |

## 与其他模块的关系

```
rh_agent ──→ GsCore AI Agent Mesh（注册能力代理画像）
         ──→ rh_generate 中的命令处理器（通过 to_ai= 注册的工具）
```

## 触发时机

模块在 Python `import` 时立即执行 [`register_rh_aigc_agent()`](RH_ComfyUI/rh_agent/__init__.py:135)，画像只在 Kanban 执行器运行时查询，因此即使注册晚于 `init_planning` 也没问题。
