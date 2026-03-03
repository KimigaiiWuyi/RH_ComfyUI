"""积分管理命令的执行函数.

该模块包含所有积分管理相关的核心逻辑函数,
包括增加积分、减少积分、查询积分等功能.
所有核心函数都同时作为 AI Tools 注册.
"""

from typing import Tuple, Optional, Annotated

from msgspec import Meta

from gsuid_core.models import Event
from gsuid_core.ai_core.register import ai_tools

from ..utils.database.models import RHBind

# ============================================================
# 参数解析函数
# ============================================================


async def parse_add_points_args(ev: Event) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """解析增加/减少积分命令的参数.

    Args:
        ev: Event 实例

    Returns:
        返回 (target_user_id, point_num, error_message)
        如果解析成功，error_message 为 None
        如果解析失败，target_user_id 和 point_num 为 None
    """
    args: list[str] = ev.text.strip().split()

    if not ev.text.strip():
        return None, None, "📋 格式: 增加积分 <@用户> <积分数量>"

    target_user_id: Optional[str] = None
    point_num: Optional[int] = None

    if ev.at:
        target_user_id = ev.at
        try:
            point_num = int(args[0])
        except (ValueError, IndexError):
            return None, None, "⚠️ 积分数量必须是数字！"
    else:
        try:
            point_num = int(args[1])
            target_user_id = args[0]
        except (ValueError, IndexError):
            return None, None, "❌ 格式错误！使用: 增加积分 <@用户> <积分数量>"

    if point_num <= 0:
        return None, None, "⚠️ 积分数量必须大于0！"

    return target_user_id, point_num, None


async def parse_query_points_args(ev: Event) -> tuple[str, Optional[str]]:
    """解析查询积分命令的参数.

    Args:
        ev: Event 实例

    Returns:
        返回 (target_user_id, error_message)
        如果解析成功，error_message 为 None
    """
    target_user_id: Optional[str] = None

    if ev.at:
        target_user_id = ev.at
    else:
        args: list[str] = ev.text.strip().split()
        if args:
            target_user_id = args[0]
        else:
            target_user_id = ev.user_id

    if ev.user_pm != 0 and ev.user_id != target_user_id:
        return target_user_id, "🚫 您不是管理员，无法查询其他用户积分！"

    return target_user_id, None


# ============================================================
# AI Tools (同时也是核心实现)
# ============================================================


def check_pm(ev: Event) -> Tuple[bool, str]:
    """检查用户是否为管理员.

    Args:
        ev: Event 实例,包含事件相关信息

    Returns:
        如果用户是管理员,返回 True;否则返回 False
    """
    if ev.user_pm == 0:
        return True, "✅ 您是管理员，为你进行操作！"
    return False, "🚫 您不是管理员，无法执行此操作！"


@ai_tools(check_func=check_pm)
async def add_user_points(
    target_user_id: Annotated[str, Meta(description="目标用户的唯一标识 ID")],
    point_num: Annotated[int, Meta(description="要增加的积分数量,必须大于 0")],
    ev: Event,
) -> str:
    """增加指定用户的积分.

    该工具用于为特定用户增加积分点数,常用于奖励用户、补偿积分或进行活动赠送.

    Args:
        target_user_id: 目标用户的唯一标识 ID
        point_num: 要增加的积分数量,必须大于 0
        ev: Event 实例,包含事件相关信息

    Returns:
        操作结果描述字符串,包含成功/失败信息和当前积分
    """
    result: int = await RHBind.add_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
        add_point_num=point_num,
    )

    if result == 0:
        current_point: int = await RHBind.get_point(
            user_id=target_user_id,
            bot_id=ev.bot_id,
        )
        return f"✅ 成功为用户 [{target_user_id}] 增加 {point_num} 积分！\n当前积分: {current_point}"
    else:
        return "❌ 增加积分失败！"


@ai_tools(check_func=check_pm)
async def deduct_user_points(
    target_user_id: Annotated[str, Meta(description="目标用户的唯一标识 ID")],
    point_num: Annotated[int, Meta(description="要扣除的积分数量,必须大于 0")],
    ev: Event,
) -> str:
    """扣除指定用户的积分.

    该工具用于从特定用户扣除积分点数,常用于消费积分、惩罚或进行积分调整.
    如果用户积分不足,将扣除全部剩余积分.

    Args:
        target_user_id: 目标用户的唯一标识 ID
        point_num: 要扣除的积分数量,必须大于 0
        ev: Event 实例,包含事件相关信息

    Returns:
        操作结果描述字符串,包含成功/失败信息和当前积分
    """
    current_point: int = await RHBind.get_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
    )

    if current_point < point_num:
        point_num = current_point

    result: bool = await RHBind.deduct_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
        deduct_point_num=point_num,
    )

    if result:
        new_point: int = await RHBind.get_point(
            user_id=target_user_id,
            bot_id=ev.bot_id,
        )
        return f"✅ 成功为用户 [{target_user_id}] 扣除 {point_num} 积分！\n当前积分: {new_point}"
    else:
        return "❌ 扣除积分失败！"


@ai_tools
async def query_user_points(
    target_user_id: Annotated[str, Meta(description="目标用户的唯一标识 ID")],
    ev: Event,
) -> str:
    """查询指定用户的当前积分.

    该工具用于获取特定用户的当前积分余额,可用于查询自己的积分或管理员查询其他用户积分.

    Args:
        target_user_id: 目标用户的唯一标识 ID
        ev: Event 实例,包含事件相关信息

    Returns:
        包含用户当前积分的描述字符串
    """
    current_point: int = await RHBind.get_point(
        user_id=target_user_id,
        bot_id=ev.bot_id,
    )

    return f"👤 用户 [{target_user_id}] 的当前积分: {current_point}"
