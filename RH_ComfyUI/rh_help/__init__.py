"""RH_ComfyUI 帮助模块"""

from gsuid_core.sv import SV, get_plugin_available_prefix
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.help.utils import register_help

sv_help = SV("RH_ComfyUI 帮助")


@sv_help.on_fullmatch("帮助", block=True)
async def send_help(bot: Bot, ev: Event) -> None:
    """发送帮助信息"""
    help_text = """📋 RH_ComfyUI 使用帮助

🖼️ 图片生成
  生图 [模型名] <描述> — 文生图/图生图
  改图 [模型名] <描述> — 图片编辑（需带图）

🎬 视频生成
  生视频 [模型名] <描述> — 文生视频/图生视频

🎵 音频生成
  生音乐 [模型名] <描述> — 音乐生成
  生语音 [模型名] <文字> — 语音合成

📋 模型信息
  模型列表 — 查看所有可用模型
  模型详情 <模型名> — 查看模型详情

💰 积分管理
  查询积分 — 查看当前积分
  增加积分 <@用户> <数量> — 管理员加积分

💡 使用技巧
  • 命令后可加模型名指定模型，如: 生图 qwen 一只猫
  • 不指定模型名则自动选择最合适的模型
  • 发图+生图命令 = 图生图，纯文字+生图 = 文生图
"""
    await bot.send(help_text)


# 注册到全局帮助一览
register_help(
    "RH_ComfyUI",
    f"{get_plugin_available_prefix('RH_ComfyUI')}帮助",
    None,  # 不传图标，使用默认
)
