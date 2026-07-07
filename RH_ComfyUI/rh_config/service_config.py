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
    GsBoolConfig,
)

# ── ComfyUI 服务 ────────────────────────────────────────────────
SERVICE_CONFIG_DEFAULT: Dict[str, GSC] = {
    "divider_comfyui": GsDivider(
        "ComfyUI 配置",
        "ComfyUI 配置",
        "ComfyUI 配置",
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
    "RH_apikey": GsStrConfig(
        "RunningHub API Key",
        "用于设置 RunningHub API Key 的配置",
        "",
    ),
    "divider_openai_image": GsDivider(
        "OpenAI 兼容生图（文生图 / 图生图 / 编辑）",
        "OpenAI 兼容协议生图服务配置（支持任意 OpenAI 兼容服务，包括 OpenAI 官方 / "
        "OneAPI / NewAPI / OpenRouter / BLT / SiliconFlow 等）",
        "OpenAI 兼容生图服务配置",
    ),
    "OpenAI_Image_apikey": GsStrConfig(
        "OpenAI 兼容生图 API Key",
        "用于设置 OpenAI 兼容生图接口的 API Key（适用于所有 OpenAI 兼容服务，"
        "包括 OpenAI / OneAPI / NewAPI / OpenRouter / BLT 等）",
        "",
        options=["sk-xxx"],
    ),
    "OpenAI_Image_BaseURL": GsStrConfig(
        "OpenAI 兼容生图 Base URL",
        "用于设置 OpenAI 兼容生图接口的 Base URL（可填任意 OpenAI 兼容服务的地址，"
        "包括 OpenAI 官方 / OneAPI / NewAPI / OpenRouter / BLT 等）",
        "https://api.openai.com/v1",
        options=[
            "https://api.openai.com/v1",
            "https://api.bltcy.ai",
        ],
    ),
    "divider_minimax": GsDivider(
        "MiniMax（文生图 / 图生图 / T2A 语音）",
        "MiniMax 文生图 / 图生图 / T2A 语音合成服务连接配置",
        "MiniMax 文生图 / 图生图 / T2A 语音合成服务连接配置",
    ),
    "MiniMax_apikey": GsStrConfig(
        "MiniMax API Key",
        "用于设置 MiniMax API 的 Key（文生图 / 图生图 / T2A 语音合成）",
        "",
        options=["eyJhbGciOiJSUzI1Ni........."],
    ),
    "divider_mimo": GsDivider(
        "MiMo TTS（小米语音合成）",
        "MiMo 语音合成服务连接配置",
        "MiMo 语音合成服务连接配置",
    ),
    "MIMO_apikey": GsStrConfig(
        "MiMo API Key",
        "用于设置 XiaoMi MiMo TTS 语音合成 API 的 Key（MiMo-V2.5-TTS 系列）",
        "",
        options=["sk-xxx"],
    ),
    "divider_seedance": GsDivider(
        "Seedance 视频生成",
        "Seedance 视频生成服务配置。每个供应商可独立启用/禁用,并配置独立的 API Key 和 Base URL。",
        "火山官方 Seedance 服务配置",
    ),
    "Seedance_apikey_ark": GsStrConfig(
        "ARK API Key",
        "火山方舟 Seedance API Key。用于 Seedance 2.0 / 2.0 Fast / 1.5 Pro / 1.0 Pro 等系列模型。",
        "",
        options=["xxxx-xxxx-xxxx"],
    ),
    "Seedance_BaseURL_ark": GsStrConfig(
        "ARK Base URL",
        "火山方舟 Seedance 官方 API 地址。默认值已填,一般无需修改。",
        "https://ark.cn-beijing.volces.com/api/v3",
        options=[
            "https://ark.cn-beijing.volces.com/api/v3",
        ],
    ),
    "Seedance_Enable_ark": GsBoolConfig(
        "启用 ARK 供应商",
        "是否启用火山方舟官方 Seedance 供应商。禁用后,该供应商不会参与任务分发。",
        True,
    ),
    "divider_seedance_runninghub": GsDivider(
        "Seedance RunningHub",
        "RunningHub 平台的 Seedance 服务配置。",
        "RunningHub Seedance 服务配置",
    ),
    "Seedance_apikey_runninghub": GsStrConfig(
        "RunningHub API Key",
        "RunningHub 平台的 Seedance API Key。为空时回退使用 RH_apikey。",
        "",
        options=["sk-xxx"],
    ),
    "Seedance_BaseURL_runninghub": GsStrConfig(
        "RunningHub Base URL",
        "RunningHub 平台的 Seedance API 地址。默认值已填,一般无需修改。",
        "https://www.runninghub.cn",
        options=[
            "https://www.runninghub.cn",
        ],
    ),
    "Seedance_Enable_runninghub": GsBoolConfig(
        "启用 RunningHub 供应商",
        "是否启用 RunningHub 供应商。禁用后,该供应商不会参与任务分发。",
        False,
    ),
    "divider_seedance_lb": GsDivider(
        "Seedance 负载均衡",
        "多供应商时的负载均衡与熔断策略配置。",
        "Seedance 负载均衡",
    ),
    "Seedance_Load_Balance": GsStrConfig(
        "负载均衡策略",
        "round_robin=轮询分发; weighted=加权随机(官方优先); least_failures=优先选连续失败最少的。"
        "仅多个供应商同时启用时生效。",
        "round_robin",
        options=["round_robin", "weighted", "least_failures"],
    ),
    "Seedance_Failure_Threshold": GsStrConfig(
        "熔断阈值",
        "供应商连续失败多少次后暂时跳过。0=不熔断。",
        "3",
        options=["1", "3", "5", "0"],
    ),
    "Seedance_Dry_Run": GsBoolConfig(
        "Dry-Run(拦截请求 + 打印)",
        "开启后,所有 Seedance 出站请求会被拦截,抛 SeedanceProviderError(code=DRY_RUN_BLOCKED) 终止;"
        "同时通过 logger.info 打印被拦截请求的完整内容(method/url/headers[脱敏]/body[全量 JSON])。"
        "不会真正发送请求,不会消耗配额。",
        False,
    ),
}
