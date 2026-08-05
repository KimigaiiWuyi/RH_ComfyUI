"""各上游服务的 API Key / BaseURL 配置。

将服务连接信息（API Key、BaseURL、地址选项）集中管理，
与插件自身行为配置（并发、积分等）解耦。

Web 控制台展示按服务分组，使用 [`GsDivider`](../../../gsuid_core/utils/plugins_config/models.py:93) 分割。
"""

from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsDivider,
    GsStrConfig,
    GsBoolConfig,
    GsRepeatGroupConfig,
)

# model_real_name 下拉项: 现有图片模型名(与 models/image/defs.py::ALL_MODELS 对齐)。
# 用静态常量避免在配置模块加载期 import defs 触发循环导入; 新增模型时同步此表即可。
_IMAGE_MODEL_REAL_NAMES = [
    "anima",
    "banana1",
    "banana2",
    "banana_pro",
    "gpt-image-2",
    "minimax_image01",
    "qwen_2511",
    "qwen_2512",
    "seedream5",
    "seedream5_pro",
]

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
        "OneAPI / NewAPI / OpenRouter / SiliconFlow 等）",
        "OpenAI 兼容生图服务配置",
    ),
    "OpenAI_Image_apikey": GsStrConfig(
        "OpenAI 兼容生图 API Key",
        "用于设置 OpenAI 兼容生图接口的 API Key（适用于所有 OpenAI 兼容服务，"
        "包括 OpenAI / OneAPI / NewAPI / OpenRouter 等）",
        "",
    ),
    "OpenAI_Image_BaseURL": GsStrConfig(
        "OpenAI 兼容生图 Base URL",
        "用于设置 OpenAI 兼容生图接口的 Base URL（可填任意 OpenAI 兼容服务的地址，"
        "包括 OpenAI 官方 / OneAPI / NewAPI / OpenRouter 等）",
        "https://api.openai.com/v1",
        options=[
            "https://api.openai.com/v1",
        ],
    ),
    "divider_gemini_image": GsDivider(
        "Gemini 生图(Interactions API,走 google-genai SDK)",
        "Gemini 3.1 Flash Image。默认走 AI Studio 个人版(只需 API Key);打开"
        "「使用 VertexAI」开关才走组织版(需 project + ADC/服务账号,不用 API Key)。",
        "Gemini 生图服务配置",
    ),
    "Gemini_Image_apikey": GsStrConfig(
        "Gemini API Key(AI Studio)",
        "AI Studio 个人版 API Key。默认模式(未开 VertexAI)用此项鉴权。",
        "",
    ),
    "Gemini_Image_BaseURL": GsStrConfig(
        "Gemini 中转地址(AI Studio,可选)",
        "服务器直连不到官方端点时填中转地址,如 https://你的中转域名/gemini/ ,"
        "SDK 会在其后拼 /v1beta/...。留空则直连官方端点。仅 AI Studio 模式生效"
        "(VertexAI 走自己的端点,不吃这项)。",
        "",
        options=[],
    ),
    "Gemini_Image_Use_Vertex": GsBoolConfig(
        "使用 VertexAI(组织版)",
        "关=AI Studio(用 API Key);开=VertexAI(用下方 Project ID + ADC/服务账号,"
        "此时忽略 API Key)。SDK 限制:project 与 api_key 互斥,只能二选一。",
        False,
    ),
    "Gemini_Image_Project_ID": GsStrConfig(
        "VertexAI Project ID",
        "填写即走 VertexAI 组织版;留空走 AI Studio 个人版。",
        "",
    ),
    "Gemini_Image_Location": GsStrConfig(
        "VertexAI 区域",
        "VertexAI 区域(仅 Project ID 非空时生效)。",
        "global",
        options=["global", "us-central1"],
    ),
    "Gemini_Image_SA_File": GsStrConfig(
        "VertexAI 服务账号 JSON 路径(可选)",
        "VertexAI 鉴权用的服务账号 JSON 文件绝对路径;留空则用环境 ADC"
        "(GOOGLE_APPLICATION_CREDENTIALS / gcloud auth application-default login)。",
        "",
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
    ),
    "divider_fishaudio": GsDivider(
        "Fish Audio 语音合成",
        "Fish Audio S2 系列 TTS 与自动音色克隆服务连接配置",
        "Fish Audio S2 系列 TTS 服务连接配置",
    ),
    "FishAudio_apikey": GsStrConfig(
        "Fish Audio API Key",
        "用于设置 Fish Audio 官方 API 的 Key（S2 系列 TTS 与音色克隆）",
        "",
    ),
    "FishAudio_Model": GsStrConfig(
        "Fish Audio 模型档位",
        "默认 s2.1-pro（官方免费期内不计费，质量最好）；s1 为旧版更稳。"
        "（营销名 s2.1-pro-free 线上会判 Unknown model，已不作为选项。）",
        "s2.1-pro",
        options=["s2.1-pro", "s2-pro", "s1"],
    ),
    "divider_seedance": GsDivider(
        "Seedance 视频生成 / Seedream 5.0 图片",
        "Seedance 视频生成 + Seedream 5.0 (Lite / Pro) 图片生成服务配置。"
        "Seedance 与 Seedream 同源火山方舟 ARK,共用同一 API Key 与 Base URL;"
        "每个供应商可独立启用/禁用,并配置独立的 API Key 和 Base URL。",
        "火山官方 Seedance / Seedream 服务配置",
    ),
    "Seedance_apikey_ark": GsStrConfig(
        "ARK API Key",
        "火山方舟 ARK API Key。"
        "用于 Seedance 2.0 / 2.0 Fast / 1.5 Pro / 1.0 Pro 视频系列,"
        "同时用于 Seedream 5.0 Lite / Pro 图片系列(同源 Key)。",
        "",
    ),
    "Seedance_BaseURL_ark": GsStrConfig(
        "ARK Base URL",
        "火山方舟 ARK 官方 API 地址(Seedance 视频端点 + Seedream 图片端点共用)。"
        "默认值已填,一般无需修改。",
        "https://ark.cn-beijing.volces.com/api/v3",
        options=[
            "https://ark.cn-beijing.volces.com/api/v3",
        ],
    ),
    "Seedance_Enable_ark": GsBoolConfig(
        "启用 ARK 供应商",
        "是否启用火山方舟官方供应商。禁用后,Seedance 视频与 Seedream 5.0 图片"
        "均不会通过该供应商分发(同源开关)。",
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
    "divider_openai_image_pool": GsDivider(
        "OpenAI 兼容生图供应商池",
        "为现有模型追加任意数量的 OpenAI 兼容生图供应商(如百度千帆)。同一内部模型可挂多家,"
        "自动参与负载均衡与熔断;调用方只看到一个模型,后端按供应商分发并入库(含 Key 前缀)。",
        "OpenAI 兼容生图供应商池",
    ),
    # 供应商池配置键按「协议_模态_Providers」命名:图片=OpenAI_Image_Providers;
    # 未来扩展语音/视频池时新增 OpenAI_Speech_Providers / OpenAI_Video_Providers,
    # 行结构(enable/name/base_url/api_key/weight/models)保持一致即可复用同一套
    # 解析与挂载逻辑(见 utils/backends/openai_image/providers.py 的池规格注释)。
    "OpenAI_Image_Providers": GsRepeatGroupConfig(
        "OpenAI 兼容生图供应商",
        "每行一家供应商:填 Base URL / API Key,并把『现有模型』映射到『供应商侧模型名』。"
        "百度千帆示例:Base URL=https://qianfan.baidubce.com/v2,模型映射 qwen_2512=qwen-image。",
        data=[],
        template={
            "enable": GsBoolConfig("启用", "是否启用该供应商", True),
            "name": GsStrConfig("供应商名称", "唯一标识(负载均衡成员名 + 审计 backend_provider)", ""),
            "base_url": GsStrConfig(
                "Base URL",
                "OpenAI 兼容生图根地址;纯文生图自动拼 /images/generations,"
                "带参考图自动拼 /images/edits(标准 multipart 协议)",
                "https://qianfan.baidubce.com/v2",
                options=["https://qianfan.baidubce.com/v2", "https://api.openai.com/v1"],
            ),
            "api_key": GsStrConfig("API Key", "供应商访问令牌(作 Bearer)", "", secret=True),
            "weight": GsStrConfig(
                "负载权重",
                "weighted 负载均衡策略下的相对权重(≥1 的整数;数值越大分到的请求越多);"
                "其他策略下不生效",
                "1",
                options=["1", "2", "3", "5"],
            ),
            "models": GsRepeatGroupConfig(
                "提供的模型",
                "把现有内部模型映射到该供应商侧的模型名",
                data=[],
                template={
                    "model_real_name": GsStrConfig(
                        "内部模型",
                        "从现有模型中选择(决定参数与负载均衡归属)",
                        "",
                        options=_IMAGE_MODEL_REAL_NAMES,
                    ),
                    "model_id": GsStrConfig("供应商模型名", "请求发给供应商的 model 字段(如 qwen-image)", ""),
                },
            ),
        },
    ),
    "divider_load_balance": GsDivider(
        "负载均衡",
        "多通道模型(同一模型由多家供应商提供)时的负载均衡与熔断策略,全模态通用。",
        "负载均衡",
    ),
    "Load_Balance_Mode": GsStrConfig(
        "负载均衡策略",
        "round_robin=轮询分发; weighted=加权随机(官方直连权重高); least_failures=优先选连续失败最少的。"
        "仅同一模型有多个通道同时可用时生效。",
        "round_robin",
        options=["round_robin", "weighted", "least_failures"],
    ),
    "Failure_Threshold": GsStrConfig(
        "熔断阈值",
        "通道连续失败多少次后暂时跳过(冷却期内排到末尾)。0=不熔断。",
        "3",
        options=["1", "3", "5", "0"],
    ),
    "Seedance_Dry_Run": GsBoolConfig(
        "Dry-Run(拦截请求 + 打印)",
        "开启后,所有 Seedance 出站请求会被拦截,抛 DryRunInterrupt 直接终止(不触发熔断/通道切换,"
        "已预扣的积分自动退款);同时通过 logger.info 打印被拦截请求的完整内容"
        "(method/url/headers[脱敏]/body[全量 JSON])。不会真正发送请求,不会消耗配额。",
        False,
    ),
    "divider_happyhorse": GsDivider(
        "HappyHorse 视频生成(DashScope)",
        "阿里云 DashScope HappyHorse 1.1 视频生成服务配置。"
        "对外模型名 happyhorse1.1,内部按输入自动路由 t2v / i2v / r2v / video-edit。",
        "HappyHorse DashScope 服务配置",
    ),
    "HappyHorse_apikey_dashscope": GsStrConfig(
        "DashScope API Key",
        "阿里云 DashScope / 千问AI 平台 API Key,用于 HappyHorse 1.1 视频生成。",
        "",
    ),
    "HappyHorse_BaseURL_dashscope": GsStrConfig(
        "DashScope Base URL",
        "DashScope API 根地址(不含尾斜杠)。默认华北2北京;"
        "国际站等可改为对应 region 地址。",
        "https://dashscope.aliyuncs.com/api/v1",
        options=[
            "https://dashscope.aliyuncs.com/api/v1",
            "https://dashscope-intl.aliyuncs.com/api/v1",
        ],
    ),
    "HappyHorse_Enable_dashscope": GsBoolConfig(
        "启用 DashScope 供应商",
        "是否启用 DashScope HappyHorse 通道。禁用后 happyhorse1.1 不可用。",
        True,
    ),
    "HappyHorse_Dry_Run": GsBoolConfig(
        "HappyHorse Dry-Run(拦截请求 + 打印)",
        "开启后,HappyHorse 出站请求会被拦截并打印完整请求体,不会真正调用上游。",
        False,
    ),
}
