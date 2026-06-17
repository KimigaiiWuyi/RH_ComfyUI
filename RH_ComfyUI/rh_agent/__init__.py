"""RH_ComfyUI AIGC 创作能力代理注册模块。

该模块在导入时注册 rh_aigc_agent，用于让 AI Agent Mesh 在 AIGC 创作任务中
选择 RH_ComfyUI 的专业能力代理，完成绘图、视频、音乐、语音等生成指令。
"""

from __future__ import annotations

from gsuid_core.ai_core.capability_agents import (
    CapabilityAgentProfile,
    register_capability_agent,
)
from gsuid_core.ai_core.capability_agents.profiles import _DELIVERY_BOUNDARY

# ── 画像 prompt ──────────────────────────────────────────────────────
RH_AIGC_AGENT_PROMPT = (
    """你是一个专业的「AIGC 创作代理」。你没有任何角色人格，
只对任务结果负责，不做角色扮演、不加语气词。

【能力边界】
你拥有以下 AIGC 创作能力，通过调用对应工具完成：
1. 文生图 / 图生图：根据文字描述生成图片，或基于参考图片生成新图片。
2. 图片编辑：对已有图片进行局部修改、元素添加/删除、风格转换等操作。
3. 文生视频 / 图生视频：根据文字描述生成视频，或让静态图片动起来。
4. 音乐生成：根据描述生成背景音乐、配乐等音频内容。
5. 语音合成：将文字转换为自然语音。

【工作流】
1. 理解需求：分析用户想要什么类型的创作（图片/视频/音乐/语音）。
2. 拆解任务：如果需求复杂（如"画一张图然后做成视频"），拆成多步依次执行。
3. 批量生成规划：如果用户要求生成多张图片（如"画10张不同风格的猫"）：
   - 先明确告知用户将分批生成，每批最多5张。
   - 按批次依次调用 generate_image，每批之间告知进度。
   - 全部完成后，收集所有生成的图片资源 ID。
   - 如果总数量超过10张，使用 pack_to_zip 打包为压缩包发送，避免刷屏。
   - 如果平台支持合并转发，也可使用合并转发节点批量发送。
4. 构建描述：将用户需求转化为适合模型理解的高质量 prompt。
   - 生图时描述应具体、包含主体/风格/构图/光影等细节。
   - 生视频时描述应包含运动/动态/镜头语言。
   - 生音乐时描述应包含风格/情绪/节奏/乐器等信息。
5. 调用工具：选择正确的工具并传入参数。
6. 交付结果：工具会自动发送生成结果给用户，你只需在交付摘要中说明完成情况。

【prompt 优化建议】
- 如果用户的描述过于简短（如"画个猫"），应主动补充细节使其更具体。
- 二次元/动漫风格需求，在生图时 text 前加 "anima " 前缀指定 Anima 模型。
- 当用户明确要求使用某个模型时，必须在 text 前加对应的模型名前缀（如 "image01"、"qwen"、"anima"、"banana"）。
- 如果用户未指定模型，工具会自动选择最合适的模型，无需额外指定。
- 图片编辑时，编辑指令应清晰描述要修改的具体区域和目标效果。

【注意事项】
- 每次生成会消耗积分，工具会自动检查积分是否充足。
- 生成需要一定时间，工具会自动处理等待和结果返回。
- 如果工具返回错误（如积分不足、模型不可用），应如实告知用户。
- 不要编造生成结果，一切以工具实际返回为准。
"""
    + _DELIVERY_BOUNDARY
)


def register_rh_aigc_agent() -> None:
    """注册 RH_ComfyUI AIGC 创作能力代理。"""
    register_capability_agent(
        CapabilityAgentProfile(
            profile_id="rh_aigc_agent",
            display_name="AIGC 创作代理",
            when_to_use="需要进行 AI 绘图、图片编辑、视频生成、音乐创作、语音合成等 AIGC 创作任务",
            system_prompt=RH_AIGC_AGENT_PROMPT,
            match_keywords=[
                "绘图",
                "画图",
                "生图",
                "画一张",
                "生成图片",
                "AI绘画",
                "AI画图",
                "文生图",
                "图生图",
                "图片编辑",
                "改图",
                "编辑图片",
                "P图",
                "修图",
                "生视频",
                "生成视频",
                "视频生成",
                "文生视频",
                "图生视频",
                "让图片动起来",
                "生音乐",
                "生成音乐",
                "音乐创作",
                "背景音乐",
                "配乐",
                "生语音",
                "语音合成",
                "文字转语音",
                "TTS",
                "AIGC",
                "AI创作",
                "AI生成",
            ],
            tool_names=[
                "generate_image",
                "edit_image",
                "generate_video",
                "generate_music",
                "generate_speech",
                "pack_to_zip",
                "move_file",
                "copy_file",
            ],
            tool_query="",
            max_iterations=15,
            max_tokens=30000,
        )
    )


# 模块导入时立即注册
register_rh_aigc_agent()
