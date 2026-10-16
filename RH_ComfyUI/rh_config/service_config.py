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
    GsListStrConfig,
    GsRepeatGroupConfig,
)

from .fish_models import FISH_MODEL_NAMES
from ..utils.backends.kling.catalog import (
    DEFAULT_ENDPOINT,
    KLING_MODEL_NAMES,
    SINGAPORE_ENDPOINT,
)
from ..utils.backends.dashscope.config import DASHSCOPE_MODEL_OPTIONS
from ..utils.backends.azure_image.catalog import AZURE_IMAGE_MODEL_NAMES, AZURE_IMAGE_DEFAULT_MODELS
from ..utils.backends.gemini_image.config import GEMINI_MODEL_OPTIONS

# 下拉项用静态常量,避免配置模块加载期 import defs 触发循环导入;新增模型时同步此表。
_IMAGE_MODEL_REAL_NAMES = [
    "anima",
    "banana1",
    "banana2",
    "banana2.1",
    "banana2.lite",
    "banana_pro",
    "gpt-image-2",
    "minimax_image01",
    "qwen_2511",
    "qwen_2512",
    "seedream5",
    "seedream5_pro",
]
_COMFYUI_WORKFLOW_NAMES = [
    "qwen_2511",
    "qwen_2512",
    "wan2.2_videogen",
    "IndexTTS2",
    "ace_step1.5",
]
_RH_APP_NAMES = [
    "anima",
    "rh_camera_angle",
    "rh_image_matting",
    "rh_image_upscale",
    "rh_image_outpaint",
    "IndexTTS2.5",
]
_GEMINI_MODEL_NAMES = list(GEMINI_MODEL_OPTIONS)
_MINIMAX_MODEL_NAMES = ["minimax_t2a_speech", "minimax_image01", "minimax_h3"]
_MIMO_MODEL_NAMES = ["mimo_tts"]
_SEEDANCE_ARK_MODEL_NAMES = [
    "seedance15_pro",
    "seedance2",
    "seedance2.5",
    "seedance2_mini",
    "seedance2_fast",
    "seedream5",
    "seedream5_pro",
]
_SEEDANCE_RH_MODEL_NAMES = [
    "seedance15_pro",
    "seedance2",
    "seedance2_mini",
    "seedance2_fast",
]
_DASHSCOPE_MODEL_NAMES = list(DASHSCOPE_MODEL_OPTIONS)
_TX_AIART_MODEL_NAMES = ["tx_image_outpaint"]

# ── ComfyUI 服务 ────────────────────────────────────────────────
SERVICE_CONFIG_DEFAULT: Dict[str, GSC] = {
    "divider_comfyui": GsDivider(
        "ComfyUI / RunningHub AI 应用",
        "本地/远程 ComfyUI 与 RunningHub AI 应用。须在上方启用列表勾选后才分发。留空则全部不启用。增删列表即时生效。",
        "ComfyUI 服务配置",
    ),
    "ComfyUI_Enabled_Workflows": GsListStrConfig(
        "启用的 ComfyUI 工作流",
        "从列表勾选要启用的 ComfyUI 工作流;可自由添加内部模型名或 json 文件名。"
        "留空则全部不启用。改完即时生效,无需重启。"
        "可选:qwen_2511 / qwen_2512 / wan2.2_videogen / IndexTTS2 / ace_step1.5。",
        list(_COMFYUI_WORKFLOW_NAMES),
        options=list(_COMFYUI_WORKFLOW_NAMES),
    ),
    "RH_App_Enabled_Apps": GsListStrConfig(
        "启用的 RunningHub AI 应用",
        "从列表勾选要启用的 RH AI 应用;可自由添加内部模型名或 webappId。"
        "默认全部启用。留空则全部不启用。改完即时生效,无需重启。"
        "可选:anima / rh_camera_angle / rh_image_matting / "
        "rh_image_upscale / rh_image_outpaint / IndexTTS2.5。",
        list(_RH_APP_NAMES),
        options=list(_RH_APP_NAMES),
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
    "divider_gemini_image": GsDivider(
        "Gemini 生图 / 语音(generate_content,走 google-genai SDK)",
        "Gemini 生图(banana1 / banana2 / banana2.1 / banana2.lite / banana_pro 的 Gemini 通道)与语音 gemini_tts。"
        "banana2、banana2.1 与 banana2.lite 是三个模型,价格和参数都不同,须分别勾选。"
        "gemini_tts 是语音合成,须单独勾选,不走生图通道。"
        "默认 AI Studio(API Key);打开 VertexAI 走组织版。"
        "须打开「启用 Gemini 供应商」,并在模型列表勾选后才走 Gemini 通道。",
        "Gemini 生图 / 语音服务配置",
    ),
    "Gemini_Enable": GsBoolConfig(
        "启用 Gemini 供应商",
        "是否启用 Gemini 生图通道。关闭后 banana1 / banana2 / banana2.1 / "
        "banana2.lite / banana_pro / gemini_tts 均不走 Gemini。",
        True,
    ),
    "Gemini_Enabled_Models": GsListStrConfig(
        "启用的 Gemini 模型",
        "从列表勾选要走 Gemini 通道的内部模型;可自由添加内部模型名。"
        "留空则全部不走 Gemini。共用下方 Gemini 凭证。"
        "可选:banana1 / banana2 / banana2.1 / banana2.lite / banana_pro / gemini_tts。"
        "已保存的旧列表不会自动勾上 banana2.1、banana2.lite 或 gemini_tts,需要手动加上。"
        "gemini_tts 是语音合成,须单独勾选,不走生图通道。"
        "banana2.lite 只生成 1K。"
        "banana_pro 未勾选时仍可通过 OpenAI 兼容供应商池使用。",
        list(_GEMINI_MODEL_NAMES),
        options=list(_GEMINI_MODEL_NAMES),
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
        "MiniMax（文生图 / 图生图 / T2A 语音 / H3 视频）",
        "MiniMax 文生图 / 图生图 / T2A 语音合成 / H3 视频生成服务连接配置",
        "MiniMax 文生图 / 图生图 / T2A 语音合成服务连接配置",
    ),
    "MiniMax_Enable": GsBoolConfig(
        "启用 MiniMax 供应商",
        "是否启用官方 MiniMax 通道。关闭后文生图 / T2A / 官方 H3 通道不参与分发;minimax_h3 若已挂外部插件通道仍可用。",
        True,
    ),
    "MiniMax_Enabled_Models": GsListStrConfig(
        "启用的 MiniMax 模型",
        "勾选要走官方 MiniMax 通道的模型;可自由添加内部模型名。"
        "留空则官方通道全关。minimax_h3 若已挂外部插件通道,模型本身仍可用。"
        "可选:minimax_t2a_speech / minimax_image01 / minimax_h3。",
        list(_MINIMAX_MODEL_NAMES),
        options=list(_MINIMAX_MODEL_NAMES),
    ),
    "MiniMax_apikey": GsStrConfig(
        "MiniMax API Key",
        "用于设置 MiniMax API 的 Key（文生图 / 图生图 / T2A 语音合成 / H3 视频生成共用）",
        "",
    ),
    "divider_mimo": GsDivider(
        "MiMo TTS（小米语音合成）",
        "MiMo 语音合成服务连接配置",
        "MiMo 语音合成服务连接配置",
    ),
    "MIMO_Enable": GsBoolConfig(
        "启用 MiMo 供应商",
        "是否启用 MiMo TTS。关闭后 mimo_tts 不可用。",
        True,
    ),
    "MIMO_Enabled_Models": GsListStrConfig(
        "启用的 MiMo 模型",
        "从列表勾选要启用的 MiMo 模型;可自由添加内部模型名。留空则全部不启用。共用下方 MiMo API Key。可选:mimo_tts。",
        list(_MIMO_MODEL_NAMES),
        options=list(_MIMO_MODEL_NAMES),
    ),
    "MIMO_apikey": GsStrConfig(
        "MiMo API Key",
        "用于设置 XiaoMi MiMo TTS 语音合成 API 的 Key（MiMo-V2.5-TTS 系列）",
        "",
    ),
    "divider_fishaudio": GsDivider(
        "Fish Audio 语音合成",
        "Fish Audio TTS / ASR。每个档位是独立模型,可同时启用多个,共用下方 API Key。",
        "Fish Audio TTS / ASR 服务连接配置",
    ),
    "FishAudio_Enable": GsBoolConfig(
        "启用 Fish Audio 供应商",
        "是否启用 Fish Audio。关闭后已勾选的档位均不可用。",
        True,
    ),
    "FishAudio_Enabled_Models": GsListStrConfig(
        "启用的 Fish Audio 模型",
        "勾选要启用的档位,可多选。留空则全部不启用。共用下方 Fish Audio API Key。"
        "语音:s2-pro / s2.1-pro / s2.1-pro-free / drama-3-preview。"
        "识别:transcribe-1 / transcribe-1-pro。",
        list(FISH_MODEL_NAMES),
        options=list(FISH_MODEL_NAMES),
    ),
    "FishAudio_apikey": GsStrConfig(
        "Fish Audio API Key",
        "用于设置 Fish Audio 官方 API 的 Key（TTS、音色克隆与语音识别）",
        "",
    ),
    "divider_seedance": GsDivider(
        "Seedance 视频生成 / Seedream 5.0 图片",
        "Seedance 视频生成 + Seedream 5.0 (Lite / Pro) 图片生成服务配置。"
        "Seedance 与 Seedream 同源火山方舟 ARK,共用同一 API Key 与 Base URL;"
        "每个供应商可独立启用/禁用,并配置独立的 API Key 和 Base URL。",
        "火山官方 Seedance / Seedream 服务配置",
    ),
    "Seedance_Enable_ark": GsBoolConfig(
        "启用 ARK 供应商",
        "是否启用火山方舟官方供应商。禁用后,Seedance 视频与 Seedream 5.0 图片均不会通过该供应商分发(同源开关)。",
        True,
    ),
    "Seedance_Enabled_Models": GsListStrConfig(
        "启用的 ARK 模型",
        "从列表勾选要走火山方舟 ARK 的内部模型;可自由添加内部模型名。"
        "留空则全部不走 ARK。须同时打开上方「启用 ARK 供应商」。"
        "可选:seedance15_pro / seedance2 / seedance2.5 / seedance2_mini / "
        "seedance2_fast / seedream5 / seedream5_pro。",
        list(_SEEDANCE_ARK_MODEL_NAMES),
        options=list(_SEEDANCE_ARK_MODEL_NAMES),
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
        "火山方舟 ARK 官方 API 地址(Seedance 视频端点 + Seedream 图片端点共用)。默认值已填,一般无需修改。",
        "https://ark.cn-beijing.volces.com/api/v3",
        options=[
            "https://ark.cn-beijing.volces.com/api/v3",
        ],
    ),
    "divider_seedance_runninghub": GsDivider(
        "Seedance RunningHub",
        "RunningHub 平台的 Seedance 服务配置。",
        "RunningHub Seedance 服务配置",
    ),
    "Seedance_Enable_runninghub": GsBoolConfig(
        "启用 RunningHub 供应商",
        "是否启用 RunningHub 供应商。禁用后,该供应商不会参与任务分发。",
        False,
    ),
    "Seedance_Enabled_Models_runninghub": GsListStrConfig(
        "启用的 RunningHub Seedance 模型",
        "从列表勾选要走 RunningHub Seedance 的内部模型;可自由添加内部模型名。"
        "留空则全部不走该供应商。须同时打开上方「启用 RunningHub 供应商」。"
        "可选:seedance15_pro / seedance2 / seedance2_mini / seedance2_fast。",
        list(_SEEDANCE_RH_MODEL_NAMES),
        options=list(_SEEDANCE_RH_MODEL_NAMES),
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
    "divider_openai_image_pool": GsDivider(
        "OpenAI 兼容生图供应商池",
        "为现有图片模型追加任意数量的 OpenAI 兼容生图供应商(官方 / OneAPI / NewAPI / 千帆等)。"
        "在下方为每家供应商填写 Key 与模型映射;同一内部模型可挂多家,自动负载均衡。"
        "关闭「启用 OpenAI 兼容生图」后整池不分发。原独立 Key/URL 已并入本池。",
        "OpenAI 兼容生图供应商池",
    ),
    "OpenAI_Image_Enable": GsBoolConfig(
        "启用 OpenAI 兼容生图",
        "是否启用供应商池。关闭后池内所有供应商都不分发。",
        True,
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
                "weighted 负载均衡策略下的相对权重(≥1 的整数;数值越大分到的请求越多);其他策略下不生效",
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
    "divider_azure_image": GsDivider(
        "Azure GPT-Image",
        "Azure OpenAI 生图,挂到 gpt-image-2 / gpt-image-2.5-sunburst / gpt-image-2.5-flare。"
        "部署名必须与 Foundry 里的 deployment 完全一致。",
        "Azure GPT-Image",
    ),
    "Azure_Image_Enable": GsBoolConfig(
        "启用 Azure GPT-Image",
        "关闭后不参与 gpt-image 家族的负载均衡。",
        False,
    ),
    "Azure_Image_Deployment": GsListStrConfig(
        "Azure 服务的模型",
        "勾选走本通道的模型。每项同时是 Foundry 部署名,"
        "须与门户 deployment 完全一致"
        "(gpt-image-2 / gpt-image-2.5-sunburst / gpt-image-2.5-flare)。"
        "默认勾选 2.5 两款。未勾选的不走本通道。改完即时生效。",
        list(AZURE_IMAGE_DEFAULT_MODELS),
        options=list(AZURE_IMAGE_MODEL_NAMES),
    ),
    "Azure_Image_apikey": GsStrConfig(
        "Azure API Key",
        "Azure OpenAI 资源的 API Key。为空则本通道不可用。",
        "",
    ),
    "Azure_Image_Endpoint": GsStrConfig(
        "Azure 终结点",
        "Azure OpenAI 终结点,如 https://xxx.openai.azure.com(不含路径)。",
        "",
        options=["https://xxx.openai.azure.com"],
    ),
    "Azure_Image_ApiVersion": GsStrConfig(
        "API 版本",
        "Azure /openai/v1/images 的 api-version。v1 路径用 preview;旧值 2025-04-01-preview 会自动改写成 preview。",
        "preview",
    ),
    "Azure_Image_Max_Concurrency": GsStrConfig(
        "最大并发",
        "在途请求达到此数则本通道暂时不可用,交给其它通道。按同一 Azure 资源计,勾选的多个部署共用这个上限。",
        "2",
    ),
    "divider_kling": GsDivider(
        "可灵 Kling",
        "可灵视频与图片。优先填 API Key;留空时用 Access Key 与 Secret Key 签发 JWT。"
        "关闭「启用可灵」后不参与分发。改完即时生效。",
        "可灵",
    ),
    "Kling_Enable": GsBoolConfig(
        "启用可灵",
        "关闭后视频和图片都不走可灵。",
        False,
    ),
    "Kling_Enabled_Models": GsListStrConfig(
        "启用的可灵模型",
        "勾选要开放的模型。视频:kling-3.0-turbo / kling-3.0 / kling-o1 / kling-2.6 / kling-2.5-turbo。"
        "图片:kling-image-3.0 / kling-image-o1 / kling-image-common(通用) / kling-image-2.1。"
        "未勾选的不可用。改完即时生效。",
        list(KLING_MODEL_NAMES),
        options=list(KLING_MODEL_NAMES),
    ),
    "Kling_API_Key": GsStrConfig(
        "API Key",
        "可灵 API Key。填写后直接作为 Bearer。与下方 Access Key 同时存在时,以 API Key 为准。",
        "",
        secret=True,
    ),
    "Kling_Access_Key": GsStrConfig(
        "Access Key",
        "旧版 Access Key。仅当 API Key 为空,且 Secret Key 也填写时,用来签发 JWT。",
        "",
        secret=True,
    ),
    "Kling_Secret_Key": GsStrConfig(
        "Secret Key",
        "旧版 Secret Key,与 Access Key 成对使用。",
        "",
        secret=True,
    ),
    "Kling_Endpoint": GsStrConfig(
        "接口地址",
        "可灵 API 根地址,不含路径。默认北京;新加坡站点可改成对应域名。",
        DEFAULT_ENDPOINT,
        options=[DEFAULT_ENDPOINT, SINGAPORE_ENDPOINT],
    ),
    "Kling_Max_Concurrency": GsStrConfig(
        "最大并发",
        "在途请求达到此数则暂时不可用。视频和图片共用这个上限。",
        "2",
    ),
    "divider_happyhorse": GsDivider(
        "DashScope(HappyHorse / 万相 3.0 / Qwen-Audio TTS)",
        "阿里云 DashScope / 千问AI:HappyHorse 1.1、万相 3.0 与"
        " qwen-audio-3.0-tts-flash / qwen-audio-3.0-tts-plus / qwen-audio-3.1-tts-flash 共用下方 API Key。"
        "须在上方启用列表勾选后才分发;供应商总开关关闭则全部不可用。"
        "改完即时生效。",
        "DashScope 视频 / 语音服务配置",
    ),
    "HappyHorse_Enable_dashscope": GsBoolConfig(
        "启用 DashScope 供应商",
        "是否启用官方 DashScope 通道。禁用后官方 happyhorse1.1 / wan3.0 /"
        " qwen-audio-3.0-tts-flash / qwen-audio-3.0-tts-plus / qwen-audio-3.1-tts-flash 通道不参与分发;"
        "若已挂外部插件通道,模型仍可用。",
        True,
    ),
    "DashScope_Enabled_Models": GsListStrConfig(
        "启用的 DashScope 模型",
        "勾选要走官方 DashScope 通道的模型;可自由添加内部模型名。"
        "留空则官方通道全关。happyhorse1.1 / wan3.0 若已挂外部插件通道,模型本身仍可用。"
        "可选:happyhorse1.1 / wan3.0 / qwen-audio-3.0-tts-flash / qwen-audio-3.0-tts-plus / qwen-audio-3.1-tts-flash。"
        "这三个 qwen-audio 名称是语音合成,已保存的旧列表不会自动勾上,需要手动加上。",
        list(_DASHSCOPE_MODEL_NAMES),
        options=list(_DASHSCOPE_MODEL_NAMES),
    ),
    "HappyHorse_apikey_dashscope": GsStrConfig(
        "DashScope API Key",
        "阿里云 DashScope / 千问AI 平台 API Key,HappyHorse 1.1、万相 3.0 与"
        " qwen-audio-3.0-tts-flash / qwen-audio-3.0-tts-plus / qwen-audio-3.1-tts-flash 共用。",
        "",
    ),
    "HappyHorse_BaseURL_dashscope": GsStrConfig(
        "DashScope Base URL",
        "DashScope API 根地址(不含尾斜杠)。默认华北2北京;国际站等可改为对应 region 地址。"
        "语音合成的 WebSocket 换成同主机的 /api-ws/v1/inference;这里填不了带业务空间 ID 的 maas 域名。",
        "https://dashscope.aliyuncs.com/api/v1",
        options=[
            "https://dashscope.aliyuncs.com/api/v1",
            "https://dashscope-intl.aliyuncs.com/api/v1",
        ],
    ),
    "divider_tx_aiart": GsDivider(
        "腾讯云混元扩图",
        "腾讯云混元 ImageOutpainting(aiart.tencentcloudapi.com)。"
        "须打开「启用腾讯云混元」,勾选模型,并填写 SecretId / SecretKey 后可用。",
        "腾讯云混元扩图",
    ),
    "TX_AIArt_Enable": GsBoolConfig(
        "启用腾讯云混元",
        "是否启用腾讯云混元扩图。关闭后 tx_image_outpaint 不可用。",
        True,
    ),
    "TX_AIArt_Enabled_Models": GsListStrConfig(
        "启用的腾讯云混元模型",
        "从列表勾选要启用的混元扩图模型;可自由添加内部模型名。"
        "留空则全部不启用。共用下方腾讯云凭证。"
        "可选:tx_image_outpaint。",
        list(_TX_AIART_MODEL_NAMES),
        options=list(_TX_AIART_MODEL_NAMES),
    ),
    "TX_AIArt_secret_id": GsStrConfig(
        "腾讯云 SecretId",
        "腾讯云 API 密钥 SecretId(用于混元 ImageOutpainting 扩图)。",
        "",
        secret=True,
    ),
    "TX_AIArt_secret_key": GsStrConfig(
        "腾讯云 SecretKey",
        "腾讯云 API 密钥 SecretKey(用于混元 ImageOutpainting 扩图)。",
        "",
        secret=True,
    ),
    "TX_AIArt_region": GsStrConfig(
        "腾讯云地域",
        "混元 AI 艺术接口地域,一般保持默认即可。",
        "ap-guangzhou",
        options=["ap-guangzhou", "ap-beijing", "ap-shanghai"],
    ),
}
