from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsIntConfig,
    GsStrConfig,
)

CONFIG_DEFAULT: Dict[str, GSC] = {
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
