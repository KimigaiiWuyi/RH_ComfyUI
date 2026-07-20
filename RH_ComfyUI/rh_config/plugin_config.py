"""插件自身行为配置。

与上游服务无关的插件设置：并发上限、用户初始积分、各种业务的积分消耗等。

Web 控制台展示按用途分组，使用 [`GsDivider`](../../../gsuid_core/utils/plugins_config/models.py:93) 分割。
"""

from __future__ import annotations

from typing import Dict

from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsDivider,
    GsIntConfig,
)

PLUGIN_CONFIG_DEFAULT: Dict[str, GSC] = {
    "_divider_runtime": GsDivider(
        "运行参数",
        "插件运行时行为参数",
    ),
    "Channel_Concurrency": GsIntConfig(
        "供应商通道并发数（每通道一把闸，作为 (model, channel) 闸的基线）",
        "所有供应商通道（runninghub / rh_app / comfyui / ark / gateway / aifoundation / "
        "azure / fishaudio / minimax / gemini-image / gpt-image-2 / mimo 等）共享此基线；"
        "本地 ComfyUI 工作流自带 max_concurrency=1，会被 (model, channel) 闸自动收窄为 1。"
        "改动即刻生效",
        10,
        options=[5, 10, 20, 50],
    ),
    "Dispatch_Timeout": GsIntConfig(
        "单任务超时预算（秒）",
        "一次生成从进入排队到完成的总时长上限；超时按失败处理（落统计并退款），"
        "防止卡死的上游长期占用并发闸。0=不限制。改动即刻生效",
        1800,
        options=[0, 600, 1200, 1800, 3600],
    ),
    "_divider_point": GsDivider(
        "积分规则",
        "新用户初始积分 / 各业务积分消耗",
    ),
    "Default_Point": GsIntConfig(
        "默认初始积分",
        "新用户首次绑定时获得的初始积分",
        20,
        options=[10, 20, 30, 50],
    ),
    "Draw_Point": GsIntConfig(
        "绘图积分消耗",
        "每次绘图（文生图 / 图生图）消耗的积分",
        2,
        options=[5, 10, 15, 20],
    ),
    "Edit_Image_Point": GsIntConfig(
        "编辑图片积分消耗",
        "每次图片编辑消耗的积分",
        4,
        options=[5, 10, 15, 20],
    ),
    "_divider_media": GsDivider(
        "多媒体积分消耗",
        "音乐 / 语音 / 视频生成的积分消耗",
    ),
    "Music_Point": GsIntConfig(
        "生成音乐积分消耗",
        "每次生成音乐消耗的积分",
        2,
        options=[5, 10, 15, 20],
    ),
    "Speech_Point": GsIntConfig(
        "生成语音积分消耗",
        "每次生成语音消耗的积分",
        2,
        options=[5, 10, 15, 20],
    ),
    "Video_Point": GsIntConfig(
        "生成视频积分消耗",
        "每次生成视频消耗的积分",
        15,
        options=[5, 10, 15, 20],
    ),
}
