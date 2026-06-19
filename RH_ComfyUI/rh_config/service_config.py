"""各上游服务的 API Key / BaseURL 配置。

将服务连接信息（API Key、BaseURL、地址选项）集中管理，
与插件自身行为配置（并发、积分等）解耦。

前端展示按服务分组，使用 [`GsDivider`](../../../gsuid_core/utils/plugins_config/models.py:93) 分割。
"""

from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsDivider,
    GsStrConfig,
)

# ── ComfyUI 服务 ────────────────────────────────────────────────
SERVICE_CONFIG_DEFAULT: Dict[str, GSC] = {
    "_divider_comfyui": GsDivider(
        "ComfyUI 本地服务",
        "ComfyUI 本地服务连接配置",
    ),
    "ComfyUI_BaseURL": GsStrConfig(
        "ComfyUI 服务地址",
        "用于设置 ComfyUI Server Address 的配置",
        "127.0.0.1:8188",
        options=[
            "使用RunningHub代理",
            "127.0.0.1:8188",
        ],
    ),
    "_divider_rh": GsDivider(
        "RunningHub（RH）",
        "RunningHub 平台服务连接配置",
    ),
    "RH_apikey": GsStrConfig(
        "RunningHub API Key",
        "用于设置 RunningHub API Key 的配置",
        "",
    ),
    "_divider_minimax": GsDivider(
        "MiniMax（文生图 / 图生图 / T2A 语音）",
        "MiniMax 文生图 / 图生图 / T2A 语音合成服务连接配置",
    ),
    "MiniMax_apikey": GsStrConfig(
        "MiniMax API Key",
        "用于设置 MiniMax API 的 Key（文生图 / 图生图 / T2A 语音合成）",
        "",
        options=["eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."],
    ),
    "_divider_mimo": GsDivider(
        "MiMo TTS（小米语音合成）",
        "MiMo 语音合成服务连接配置",
    ),
    "MIMO_apikey": GsStrConfig(
        "MiMo API Key",
        "用于设置 XiaoMi MiMo TTS 语音合成 API 的 Key（MiMo-V2.5-TTS 系列）",
        "",
        options=["sk-xxx"],
    ),
    "_divider_seedance": GsDivider(
        "Seedance（火山方舟视频生成）",
        "火山方舟 Seedance 视频生成服务连接配置",
    ),
    "Seedance_apikey": GsStrConfig(
        "Seedance API Key (ARK)",
        "用于设置火山方舟 Seedance 2.0 视频生成 API 的 Key（Seedance 2.0 / 2.0 Fast / 1.5 Pro / 1.0 Pro 等系列）",
        "",
        options=["xxxx-xxxx-xxxx"],
    ),
    "Seedance_BaseURL": GsStrConfig(
        "Seedance Base URL",
        "火山方舟 Seedance API 的 Base URL，一般无需修改",
        "https://ark.cn-beijing.volces.com/api/v3",
        options=["https://ark.cn-beijing.volces.com/api/v3"],
    ),
    "_divider_openai_image": GsDivider(
        "OpenAI 兼容生图（文生图 / 图生图 / 编辑）",
        "OpenAI 兼容协议生图服务配置（支持任意 OpenAI 兼容服务，包括 OpenAI 官方 / OneAPI / NewAPI / OpenRouter / BLT / SiliconFlow 等）",
    ),
    "OpenAI_Image_apikey": GsStrConfig(
        "OpenAI 兼容生图 API Key",
        "用于设置 OpenAI 兼容生图接口的 API Key（适用于所有 OpenAI 兼容服务，包括 OpenAI / OneAPI / NewAPI / OpenRouter / BLT 等）",
        "",
        options=["sk-xxx"],
    ),
    "OpenAI_Image_BaseURL": GsStrConfig(
        "OpenAI 兼容生图 Base URL",
        "用于设置 OpenAI 兼容生图接口的 Base URL（可填任意 OpenAI 兼容服务的地址，包括 OpenAI 官方 / OneAPI / NewAPI / OpenRouter / BLT 等）",
        "https://api.openai.com/v1",
        options=[
            "https://api.openai.com/v1",
            "https://api.bltcy.ai",
        ],
    ),
}
