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

🖼️ 图片生成(imagegen)
  生图 [模型名] <描述> — 图片生成(自动适配:不传图=文生、1+张=编辑)
  • 同一模型节点同时支持文生图与图片编辑(按是否带图自动切换)
  • 带图场景下支持的参考图数量:banana2/banana_pro/qwen_2511 ≤ 3 张;gpt-image-2 可接受多张

🎬 视频生成(videogen)
  生视频 [模型名] <描述> — 视频生成(自动适配:不传图=文生、1张=图生、≥2张=首尾帧/多模态)
  • prompt 中可用 "图片1"/"图片2" 代号引用下方传入的参考图
  • 带多张图 + 音/视频参考 → 多模态视频生成(以 Seedance 为佳)

🎵 音频生成
  生音乐 [模型名] <描述> — 音乐生成
  生语音 [模型名] <文字> — 语音合成

📋 模型信息
  模型列表 — 查看所有可用模型
  模型详情 <模型名> — 查看模型详情

💰 积分管理
  查询积分 — 查看当前积分
  消费记录 [天数] [条数] — 查看本人的任务消费记录与任务类型分布
  增加积分 <@用户> <数量> — 管理员加积分

🛡️ 管理员命令(pm=0)
  全员消费记录 [天数] [条数] [@用户/用户ID] — 查看全员/指定用户的消费记录与任务类型分布

💡 使用技巧
  • 命令后可加模型名指定模型，如: 生图 qwen 一只猫
  • 不指定模型名则自动选择最合适的模型
  • 发图+生图命令 = 图生图，纯文字+生图 = 文生图
  • 视频生成同图:不传图=文生、1张=图生、≥2张=首尾帧/多模态
  • 多模态场景可在 prompt 中用 "图片1/视频1/音频1" 等代号引用素材
"""
    await bot.send(help_text)


# 注册到全局帮助一览
register_help(
    "RH_ComfyUI",
    f"{get_plugin_available_prefix('RH_ComfyUI')}帮助",
    None,  # 不传图标，使用默认
)
