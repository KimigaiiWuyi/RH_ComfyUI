"""积分相关模块"""

from typing import Tuple

from gsuid_core.logger import logger
from gsuid_core.models import Event

from .database.models import RHBind
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

# 积分配置
Draw_Point: int = RHCOMFYUI_CONFIG.get_config("Draw_Point").data
Edit_Image_Point: int = RHCOMFYUI_CONFIG.get_config("Edit_Image_Point").data
Music_Point: int = RHCOMFYUI_CONFIG.get_config("Music_Point").data
Speech_Point: int = RHCOMFYUI_CONFIG.get_config("Speech_Point").data
Video_Point: int = RHCOMFYUI_CONFIG.get_config("Video_Point").data


async def check_point(ev: Event, point: int) -> Tuple[bool, str]:
    """检查用户是否有足够的积分"""
    logger.info(f"[RHComfyUI] check_point: 用户:{ev.user_id} BotID:{ev.bot_id} 消费:{point}")

    bind = await RHBind.deduct_point(ev.user_id, ev.bot_id, point)
    now_point = await RHBind.get_point(ev.user_id, ev.bot_id)

    if bind:
        return True, f"💪 积分充足！已扣除{point}积分!\n📋 当前积分: {now_point}\n✅ 正在生成，预计将等待1分钟..."
    else:
        return False, f"❌ 积分不足！需要{point}积分！\n📋 当前积分: {now_point}"
