from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
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
}
