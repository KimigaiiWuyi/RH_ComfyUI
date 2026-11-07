"""fal 目录数据(纯常量,不读配置不发网络)。

队列 API 根地址 ``https://queue.fal.run``;
状态/结果/取消 URL 挂在 owner+app 前缀 ``blackforestlabs/flux-3-max`` 下,
三个模式的 request_id 共用该前缀,故 vendor_task_id 只存 request_id 即可。
"""

# (内部模型名, 展示名)
FAL_VIDEO_MODELS: list[tuple[str, str]] = [("flux3max", "FLUX 3 Max")]
FAL_MODEL_NAMES: list[str] = [name for name, _ in FAL_VIDEO_MODELS]

DEFAULT_QUEUE_ENDPOINT = "https://queue.fal.run"

# 状态 / 结果 / 取消共享的 owner+app 前缀
APP_PREFIX = "blackforestlabs/flux-3-max"

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
