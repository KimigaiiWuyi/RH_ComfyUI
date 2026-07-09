"""RH_ComfyUI 积分管理模块.

该模块提供用户积分管理功能,包括:
- 增加积分
- 减少积分
- 查询积分
- 查看消费记录

支持命令行和 AI Tools 两种调用方式.
"""

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

# 导入 commands 模块触发 AI Tools 自动注册(query_task_records / task_stats_summary
# 仅作 @ai_tools 注册,不在此处使用)
from .commands import (
    add_user_points,
    query_user_points,
    deduct_user_points,
    parse_add_points_args,
    format_user_consumption,
    parse_query_points_args,
    format_admin_consumption,
)

sv_admin = SV("积分管理", pm=0)
sv_user = SV("用户积分")


@sv_admin.on_command(("刷新供应商", "刷新供应商池", "同步供应商"), block=True)
async def refresh_providers(bot: Bot, ev: Event) -> None:
    """按当前配置重新挂载 OpenAI 兼容供应商池(增删供应商/改模型映射后调用)。

    凭证(key/base_url)本身每请求实时读,无需刷新;仅供应商增删或模型映射变动需要。
    """
    from ..utils.backends.openai_image import sync_openai_image_providers
    from ..utils.backends.openai_image.providers import resolve_provider_entries

    sync_openai_image_providers()
    entries = resolve_provider_entries()
    enabled = [e for e in entries if e.enable]
    bindings = sum(len(e.models) for e in enabled)
    await bot.send(f"✅ 供应商池已刷新: {len(enabled)} 家启用, {bindings} 条模型绑定")


@sv_admin.on_command(("增加积分", "加积分"), block=True)
async def add_points(bot: Bot, ev: Event) -> None:
    """管理员增加用户积分命令处理器.

    命令格式: 增加积分 <@用户> <积分数量>
    或: 增加积分 <用户ID> <积分数量>

    Args:
        bot: Bot 实例
        ev: Event 实例
    """
    target_user_id, point_num, error_msg = await parse_add_points_args(ev)

    if error_msg:
        await bot.send(error_msg)
        return

    if target_user_id is None or point_num is None:
        await bot.send("❌ 参数解析失败！")
        return

    result: str = await add_user_points(target_user_id, point_num, ev)
    await bot.send(result)


@sv_admin.on_command(("减少积分", "扣积分"), block=True)
async def deduct_points(bot: Bot, ev: Event) -> None:
    """管理员减少用户积分命令处理器.

    命令格式: 减少积分 <@用户> <积分数量>
    或: 减少积分 <用户ID> <积分数量>

    Args:
        bot: Bot 实例
        ev: Event 实例
    """
    target_user_id, point_num, error_msg = await parse_add_points_args(ev)

    if error_msg:
        await bot.send(error_msg)
        return

    if target_user_id is None or point_num is None:
        await bot.send("❌ 参数解析失败！")
        return

    result: str = await deduct_user_points(target_user_id, point_num, ev)
    await bot.send(result)


@sv_user.on_command(("查询积分", "查看积分"), block=True)
async def query_points(bot: Bot, ev: Event) -> None:
    """查询用户积分命令处理器.

    命令格式: 查询积分
    或: 查询积分 <@用户>
    或: 查询积分 <用户ID>

    普通用户只能查询自己的积分,管理员可以查询任何用户积分.

    Args:
        bot: Bot 实例
        ev: Event 实例
    """
    target_user_id, error_msg = await parse_query_points_args(ev)

    if error_msg:
        await bot.send(error_msg)
        return

    result: str = await query_user_points(target_user_id, ev)
    await bot.send(result)


@sv_user.on_command(("消费记录", "我的记录", "积分记录", "任务记录"), block=True)
async def my_consumption(bot: Bot, ev: Event) -> None:
    """查看本人的任务消费记录(命令格式: 消费记录 [最近N天])

    支持附加参数:
      - 消费记录 7 — 仅看最近 7 天
      - 消费记录 30 — 仅看最近 30 天
      - 消费记录 10 50 — 最近 10 天 + 最多 50 条

    普通用户只能看自己;管理员@他人查询通过 sv_admin 的"用户消费记录"命令。
    """
    args = ev.text.strip().split()
    days: int | None = None
    limit: int = 10

    if args:
        try:
            days = int(args[0])
            if days <= 0:
                await bot.send("⚠️ 天数必须 > 0")
                return
        except ValueError:
            await bot.send("⚠️ 第一个参数必须是天数(整数)")
            return

    if len(args) >= 2:
        try:
            limit = int(args[1])
            if limit <= 0 or limit > 50:
                await bot.send("⚠️ 条数必须在 1~50 之间")
                return
        except ValueError:
            await bot.send("⚠️ 第二个参数必须是条数(整数)")
            return

    text = await format_user_consumption(
        user_id=ev.user_id,
        bot_id=ev.bot_id,
        limit=limit,
        days=days,
    )
    await bot.send(text)


@sv_admin.on_command(("全员消费记录", "所有人记录", "全局记录", "用户消费记录"), block=True)
async def all_consumption(bot: Bot, ev: Event) -> None:
    """管理员查看全员任务消费记录(命令格式: 全员消费记录 [最近N天] [@用户/用户ID])

    支持附加参数:
      - 全员消费记录          — 全部时间,最近 20 条
      - 全员消费记录 7        — 仅最近 7 天
      - 全员消费记录 7 50     — 最近 7 天 + 最多 50 条
      - 全员消费记录 @用户    — 仅看指定用户
      - 全员消费记录 7 @用户 30 — 最近 7 天 + 指定用户 + 最多 30 条

    pm=0 由 SV 注册时限定,非管理员命令不会路由到此处理器。
    """
    args = ev.text.strip().split()
    days: int | None = None
    limit: int = 20
    target_user_id: str | None = ev.at  # @用户 走这里

    # 文本参数解析顺序:天数 / 条数 / 用户ID
    # @用户已在 ev.at 捕获,这里只扫文本里的剩余 token
    text_tokens: list[str] = []
    for token in args:
        # 跳过纯数字:天数 / 条数
        if token.isdigit():
            if days is None:
                try:
                    days = int(token)
                    if days <= 0:
                        await bot.send("⚠️ 天数必须 > 0")
                        return
                except ValueError:
                    pass
            else:
                try:
                    limit = int(token)
                    if limit <= 0 or limit > 100:
                        await bot.send("⚠️ 条数必须在 1~100 之间")
                        return
                except ValueError:
                    pass
        else:
            text_tokens.append(token)

    if not target_user_id and text_tokens:
        target_user_id = text_tokens[0]

    if target_user_id:
        # 指定用户:复用用户视图(更聚焦)
        text = await format_user_consumption(
            user_id=target_user_id,
            bot_id=ev.bot_id,
            limit=limit,
            days=days,
        )
    else:
        text = await format_admin_consumption(
            bot_id=ev.bot_id,
            limit=limit,
            days=days,
        )
    await bot.send(text)
