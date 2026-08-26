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
    GsStrConfig,
    GsBoolConfig,
)

PLUGIN_CONFIG_DEFAULT: Dict[str, GSC] = {
    "_divider_runtime": GsDivider(
        "运行参数",
        "插件运行时行为参数",
    ),
    "Channel_Concurrency": GsIntConfig(
        "供应商通道并发数（每通道一把闸，作为 (model, channel) 闸的基线）",
        "非 RunningHub 供应商通道（ark / gateway / aifoundation / azure / fishaudio / "
        "minimax / gemini-image / gpt-image-2 / mimo 等）使用此基线；"
        "本地 ComfyUI 工作流自带 max_concurrency=1，会被 (model, channel) 闸自动收窄为 1。"
        "改动即刻生效",
        10,
        options=[5, 10, 20, 50],
    ),
    "RH_Channel_Concurrency": GsIntConfig(
        "RunningHub 共享并发数（rh_app / runninghub / comfyui 共用一把闸）",
        "这三条通道共用同一 RH 账户并发配额；上游超限返回 421 TASK_QUEUE_MAXED（请自行排队）。"
        "本地用同一把信号量串行化，超限请求排队等待而不是打到上游报错。默认 1。"
        "改动即刻生效",
        1,
        options=[1, 2, 3, 5, 10],
    ),
    "Dispatch_Timeout": GsIntConfig(
        "单任务超时预算（秒）",
        "一次生成从进入排队到完成的总时长上限；超时按失败处理（落统计并退款），"
        "防止卡死的上游长期占用并发闸。0=不限制。改动即刻生效",
        3600,
        options=[0, 600, 1200, 1800, 3600, 7200],
    ),
    "Dry_Run": GsBoolConfig(
        "Dry-Run(拦截全部请求)",
        "开启后,所有模型的出站请求都会被拦截,抛 DryRunInterrupt 直接终止"
        "(不触发熔断/通道切换,已预扣积分自动退款);不会真正调用上游。"
        "改动即刻生效。",
        False,
    ),
    "Load_Balance_Mode": GsStrConfig(
        "负载均衡策略",
        "round_robin=轮询分发; weighted=加权随机(官方直连权重高); least_failures=优先选连续失败最少的。"
        "仅同一模型有多个通道同时可用时生效。改动即刻生效。",
        "round_robin",
        options=["round_robin", "weighted", "least_failures"],
    ),
    "Failure_Threshold": GsStrConfig(
        "熔断阈值",
        "通道连续失败多少次后暂时跳过(冷却期内排到末尾)。0=不熔断。改动即刻生效。",
        "3",
        options=["1", "3", "5", "0"],
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
    # ── 三重余额额度(5h / 日 / 周);扣费从三桶同扣,可用=min ──
    "_divider_quota_tiers": GsDivider(
        "额度档位(三重余额)",
        "5 小时 / 自然日 / 自然周 三桶满额;档位 free/basic/pro/enterprise/special/unlimited "
        "与 bot_id 无关,存在 RHBind.vip_tier,HTTP/bot/agent 通用。"
        "unlimited 不走数字 cap,扣费永不拒绝",
    ),
    "Quota_Timezone": GsStrConfig(
        "额度日/周界时区",
        "自然日 0 点与周一开始的时区,默认 Asia/Shanghai",
        "Asia/Shanghai",
    ),
    "Quota_5h_Seconds": GsIntConfig(
        "5 小时桶滚动秒数",
        "自上次补满起经过多少秒再补满 5h 桶,默认 18000=5 小时",
        18000,
        options=[3600, 7200, 18000, 36000],
    ),
    # free
    "Quota_Free_5h": GsIntConfig("免费档·5小时额度", "free 档 5h 桶满额", 8000),
    "Quota_Free_Day": GsIntConfig("免费档·日额度", "free 档日桶满额", 20000),
    "Quota_Free_Week": GsIntConfig("免费档·周额度", "free 档周桶满额", 80000),
    # basic
    "Quota_Basic_5h": GsIntConfig("基础档·5小时额度", "basic 档 5h 桶满额", 20000),
    "Quota_Basic_Day": GsIntConfig("基础档·日额度", "basic 档日桶满额", 50000),
    "Quota_Basic_Week": GsIntConfig("基础档·周额度", "basic 档周桶满额", 200000),
    # pro
    "Quota_Pro_5h": GsIntConfig("专业档·5小时额度", "pro 档 5h 桶满额", 40000),
    "Quota_Pro_Day": GsIntConfig("专业档·日额度", "pro 档日桶满额", 100000),
    "Quota_Pro_Week": GsIntConfig("专业档·周额度", "pro 档周桶满额", 400000),
    # enterprise
    "Quota_Enterprise_5h": GsIntConfig("企业档·5小时额度", "enterprise 档 5h 桶满额", 80000),
    "Quota_Enterprise_Day": GsIntConfig("企业档·日额度", "enterprise 档日桶满额", 200000),
    "Quota_Enterprise_Week": GsIntConfig("企业档·周额度", "enterprise 档周桶满额", 800000),
    # special:5h 默认 50 万,日=4×5h,周=12×5h
    "Quota_Special_5h": GsIntConfig("特殊档·5小时额度", "special 档 5h 桶满额,默认 500000", 500000),
    "Quota_Special_Day": GsIntConfig("特殊档·日额度", "special 档日桶满额,默认 5h×4", 2000000),
    "Quota_Special_Week": GsIntConfig("特殊档·周额度", "special 档周桶满额,默认 5h×12", 6000000),
}
