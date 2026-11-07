"""fal 目录数据(纯常量,不读配置不发网络)。

队列 API 根地址 ``https://queue.fal.run``;
状态/结果/取消 URL 挂在 owner+app 前缀(如 ``minimax/h3-max``)下,
同一 app 的多个端点共用该前缀,故 vendor_task_id 只存 request_id 即可。
"""

# (内部模型名, 展示名)
FAL_VIDEO_MODELS: list[tuple[str, str]] = [
    ("flux3max", "FLUX 3 Max"),
    ("h3max", "MiniMax H3 Max"),
    ("h3maxturbo", "MiniMax H3 Max Turbo"),
]
# fal 也能承载这些已定义模型(模型×供应商正交;模型类在 channel_bindings 里挂 FalChannel)
FAL_SHARED_MODELS: list[str] = [
    "minimax_h3",
    "seedance2",
    "seedance2.5",
    "banana1",
    "banana2",
    "banana2.1",
    "banana2.lite",
    "banana_pro",
    "gpt-image-2",
]
FAL_MODEL_NAMES: list[str] = [name for name, _ in FAL_VIDEO_MODELS] + FAL_SHARED_MODELS

DEFAULT_QUEUE_ENDPOINT = "https://queue.fal.run"

# 模型 → 状态 / 结果 / 取消共享的 owner+app 前缀
APP_PREFIX = "blackforestlabs/flux-3-max"
APP_PREFIXES: dict[str, str] = {
    "flux3max": "blackforestlabs/flux-3-max",
    "h3max": "minimax/h3-max",
    "h3maxturbo": "minimax/h3-max-turbo",
    "minimax_h3": "minimax/h3",
    "seedance2": "bytedance/seedance-2.0",
    "seedance2.5": "bytedance/seedance-2.5",
    "banana1": "fal-ai/nano-banana",
    "banana2": "fal-ai/nano-banana-2",
    "banana2.1": "google/nano-banana-2.1",
    "banana2.lite": "google/nano-banana-2-lite",
    "banana_pro": "fal-ai/nano-banana-pro",
    "gpt-image-2": "openai/gpt-image-2",
}


def app_prefix(model_name: str) -> str:
    """模型 → 队列 owner/app 前缀;未知模型回落首个(外部插件通道同理)。"""
    return APP_PREFIXES.get(model_name, APP_PREFIX)


# task_mode → 建单端点 id(owner/app/mode)
ENDPOINT_IDS: dict[str, str] = {
    "text_to_video": "blackforestlabs/flux-3-max/text-to-video",
    "image_to_video": "blackforestlabs/flux-3-max/image-to-video",
    "keyframes_to_video": "blackforestlabs/flux-3-max/keyframes-to-video",
}

TASK_MODES = ("auto", "text_to_video", "image_to_video", "keyframes_to_video")

# 上游还有 2K / 4K,但官方只公布 720p / 1080p 的每秒单价。
# 为了按传参精确计价,目录档位只暴露这两档。
RESOLUTIONS = ("720p", "1080p")

RATIOS = ("auto", "21:9", "2:1", "16:9", "4:3", "1:1", "3:4", "9:16")

REASONING_LEVELS = ("low", "high")

# 官方时长档位:整数秒 5~20
MIN_DURATION = 5
MAX_DURATION = 20

# 关键帧上限与时间轴刻度(24fps)
MAX_KEYFRAMES = 10
FPS = 24

# ── MiniMax H3 Max / H3 Max Turbo ──────────────────────────────────────

H3_MODEL_NAMES = ("h3max", "h3maxturbo")

# task_mode → 建单端点 id
H3_ENDPOINT_IDS: dict[str, dict[str, str]] = {
    "h3max": {
        "text_to_video": "minimax/h3-max/text-to-video",
        "image_to_video": "minimax/h3-max/image-to-video",
        "reference_to_video": "minimax/h3-max/reference-to-video",
    },
    "h3maxturbo": {
        "text_to_video": "minimax/h3-max-turbo/text-to-video",
        "image_to_video": "minimax/h3-max-turbo/image-to-video",
        "reference_to_video": "minimax/h3-max-turbo/reference-to-video",
    },
}

H3_TASK_MODES = ("auto", "text_to_video", "image_to_video", "reference_to_video")

# 上游枚举是 480P/768P/1080P;目录档位小写,组包时再大写。
H3_RESOLUTIONS = ("480p", "768p", "1080p")

# auto=不随请求发(图生跟随首帧、参考模式上游默认 adaptive);adaptive 仅参考模式合法
H3_RATIOS = ("auto", "adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

# reference 模式内图片角色 / i2v 尾帧单独成帧:与 seedance frame_mode 口径一致
H3_FRAME_MODES = ("auto", "reference", "first_last", "last_only", "first_middle_last")

H3_EXPANSION_MODES = ("disabled", "balanced", "quality")

# 官方时长是浮点秒(24fps 粒度):0.92~15;target_audio_url 时至少 2s
H3_MIN_DURATION = 0.92
H3_MAX_DURATION = 15.0
H3_DEFAULT_DURATION = 5

# reference 输入上限:总文件 ≤12,类型上限 9 图 / 3 视频 / 3 音频
H3_MAX_REFERENCE_IMAGES = 9
H3_MAX_REFERENCE_VIDEOS = 3
H3_MAX_REFERENCE_AUDIOS = 3
H3_MAX_REFERENCE_FILES = 12

# ── 共享模型(MiniMax H3 官方型 / Seedance / 图像家族)的 fal 端点 ──

# minimax/h3:与普通 H3 Max 同构但无关键帧位;resolution 是 480P/768P/2K/4K
MMH3_ENDPOINTS: dict[str, str] = {
    "text_to_video": "minimax/h3/text-to-video",
    "image_to_video": "minimax/h3/image-to-video",
    "reference_to_video": "minimax/h3/reference-to-video",
}
MMH3_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
MMH3_EXPANSION_MODES = ("disabled", "fast", "balanced", "quality")
MMH3_MIN_DURATION = 5  # fal 侧整数秒 5~15(插件模型自身允许 4,4s 走不了 fal)
MMH3_MAX_DURATION = 15
MMH3_MAX_REFERENCE_IMAGES = 9
MMH3_MAX_REFERENCE_VIDEOS = 3
MMH3_MAX_REFERENCE_AUDIOS = 3
MMH3_MAX_REFERENCE_FILES = 12

# Seedance 2.0 / 2.5:t2v / i2v / reference 三端点;duration 枚举是字符串 "4".."N" 或 "auto"
SEEDANCE_FAL_ENDPOINTS: dict[str, dict[str, str]] = {
    "seedance2": {
        "text_to_video": "bytedance/seedance-2.0/text-to-video",
        "image_to_video": "bytedance/seedance-2.0/image-to-video",
        "reference_to_video": "bytedance/seedance-2.0/reference-to-video",
    },
    "seedance2.5": {
        "text_to_video": "bytedance/seedance-2.5/text-to-video",
        "image_to_video": "bytedance/seedance-2.5/image-to-video",
        "reference_to_video": "bytedance/seedance-2.5/reference-to-video",
    },
}
SEEDANCE_FAL_RATIOS = ("auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
SEEDANCE_FAL_RESOLUTIONS: dict[str, tuple[str, ...]] = {
    "seedance2": ("480p", "720p", "1080p", "4k"),
    "seedance2.5": ("480p", "720p", "1080p"),
}
SEEDANCE_FAL_DURATION: dict[str, tuple[int, int]] = {
    "seedance2": (4, 15),
    "seedance2.5": (4, 30),
}
SEEDANCE_FAL_REF_CAPS: dict[str, tuple[int, int, int, int]] = {
    # 模型 → (图, 视频, 音频, 总文件)
    "seedance2": (9, 3, 3, 12),
    "seedance2.5": (30, 10, 10, 50),
}
# 2.5 的 ref 端点支持 task 任务类型(auto/reference/editing/extension)
SEEDANCE25_TASK_MODES = ("auto", "reference", "editing", "extension")

# banana / gpt-image:文生 + /edit(传图即编辑);fal 无 web_search 等扩展参数映射
BANANA_FAL_ENDPOINTS: dict[str, str] = {
    "banana1": "fal-ai/nano-banana",
    "banana2": "fal-ai/nano-banana-2",
    "banana2.1": "google/nano-banana-2.1",
    "banana2.lite": "google/nano-banana-2-lite",
    "banana_pro": "fal-ai/nano-banana-pro",
}
# fal 收的分辨率档位(插件 image_size → fal resolution)
BANANA_FAL_RESOLUTIONS: dict[str, dict[str, str] | None] = {
    "banana1": None,  # 一代无尺寸档
    "banana2": {"512": "0.5K", "1K": "1K", "2K": "2K", "4K": "4K"},
    "banana2.1": {"1K": "1K", "2K": "2K", "4K": "4K"},
    "banana2.lite": {"1K": "1K"},
    "banana_pro": {"1K": "1K", "2K": "2K", "4K": "4K"},
}
BANANA_FAL_THINKING: dict[str, tuple[str, ...] | None] = {
    "banana1": None,
    "banana2": ("minimal", "high"),
    "banana2.1": ("minimal", "medium", "high"),
    "banana2.lite": ("minimal", "high"),
    "banana_pro": None,  # Pro 只有 high 一档,不传
}
GPT_IMAGE_FAL_ENDPOINT = "openai/gpt-image-2"
# fal gpt-image-2 显式 {width,height} 约束:16 倍数、最长边 3840、比例 ≤3:1、像素 ∈ [655360, 8294400]
GPT_IMAGE_FAL_MAX_EDGE = 3840
GPT_IMAGE_FAL_TIER_PIXELS: dict[str, int] = {
    "1K": 1024 * 1024,
    "2K": 2048 * 2048,
    "4K": 3840 * 2160,
}
