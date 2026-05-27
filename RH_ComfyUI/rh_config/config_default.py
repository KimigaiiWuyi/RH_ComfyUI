from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
    "Max_Concurrency": GsIntConfig(
        "全局最大并发数",
        "限制所有后端（RH原生/ComfyUI/BLT）同时执行的最大任务数，防止过载",
        1,
        options=[1, 2, 3, 5, 10],
    ),
    "ComfyUI_BaseURL": GsStrConfig(
        "ComfyUI 服务地址",
        "用于设置ComfyUI Server Address的配置",
        "127.0.0.1:8188",
        options=[
            "使用RunningHub代理",
            "127.0.0.1:8188",
        ],
    ),
    "RH_apikey": GsStrConfig(
        "RunningHub API Key",
        "用于设置RunningHub API Key的配置",
        "",
    ),
    "BLT_apikey": GsStrConfig(
        "BLT API Key",
        "用于设置BLT/OpenAI兼容API的API Key配置",
        "",
        options=[
            "sk-xxx",
        ],
    ),
    "BLT_API_URL": GsStrConfig(
        "BLT API URL",
        "用于设置BLT/OpenAI兼容API的Base URL配置",
        "https://api.bltcy.ai",
        [
            "https://api.bltcy.ai",
        ],
    ),
    "MiniMax_apikey": GsStrConfig(
        "MiniMax API Key",
        "用于设置MiniMax API的Key配置（文生图/图生图/T2A语音合成）",
        "",
        options=[
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
        ],
    ),
    "MIMO_apikey": GsStrConfig(
        "MiMo API Key",
        "用于设置XiaoMi MiMo TTS语音合成API的Key配置（MiMo-V2.5-TTS系列）",
        "",
        options=[
            "sk-xxx",
        ],
    ),
    "Default_Point": GsIntConfig(
        "默认初始积分",
        "用于设置新用户默认初始积分的配置",
        20,
        options=[
            10,
            20,
            30,
            50,
        ],
    ),
    "Draw_Point": GsIntConfig(
        "绘图积分消耗",
        "用于设置每次绘图消耗的积分的配置",
        2,
        options=[
            5,
            10,
            15,
            20,
        ],
    ),
    "Edit_Image_Point": GsIntConfig(
        "编辑图片积分消耗",
        "用于设置每次编辑消耗的积分的配置",
        4,
        options=[
            5,
            10,
            15,
            20,
        ],
    ),
    "Music_Point": GsIntConfig(
        "生成音乐积分消耗",
        "用于设置每次生成音乐消耗的积分的配置",
        2,
        options=[
            5,
            10,
            15,
            20,
        ],
    ),
    "Speech_Point": GsIntConfig(
        "生成语音积分消耗",
        "用于设置每次生成语音消耗的积分的配置",
        2,
        options=[
            5,
            10,
            15,
            20,
        ],
    ),
    "Video_Point": GsIntConfig(
        "生成视频积分消耗",
        "用于设置每次生成视频消耗的积分的配置",
        15,
        options=[
            5,
            10,
            15,
            20,
        ],
    ),
}
