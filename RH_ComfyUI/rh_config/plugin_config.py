"""插件自身行为配置。

与上游服务无关的插件设置：并发上限、用户初始积分、各种业务的积分消耗等。

前端展示按用途分组，使用 [`GsDivider`](../../../gsuid_core/utils/plugins_config/models.py:93) 分割。
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
    "Max_Concurrency": GsIntConfig(
        "全局最大并发数",
        "限制所有后端（RH 原生 / ComfyUI / GPT-Image2 等）同时执行的最大任务数，防止过载",
        1,
        options=[1, 2, 3, 5, 10],
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
