"""RH_ComfyUI 积分管理模块.

该模块提供用户积分管理功能,包括:
- 增加积分
- 减少积分
- 查询积分

支持命令行和 AI Tools 两种调用方式.
"""

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

# 导入 commands 模块触发 AI Tools 自动注册
from .commands import (
    add_user_points,
    query_user_points,
    deduct_user_points,
    parse_add_points_args,
    parse_query_points_args,
)

sv_admin = SV("积分管理", pm=0)
sv_user = SV("用户积分")


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
