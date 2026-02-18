from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.utils.image.convert import convert_img

from ..utils.comfyui.wrapper import gen_image_by_text
from ..utils.database.models import RHBind

sv_draw = SV("AI绘图")


@sv_draw.on_command(("文生图",), block=True)
async def draw_img(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要绘图的prompt！")

    if await RHBind.deduct_point(ev.user_id, ev.bot_id, 1):
        await bot.send("💪 积分充足！已扣除1点积分!\n✅ 正在生成图片，预计将等待1分钟...")
        image = await gen_image_by_text(prompt)

        await bot.send("✅ 图片生成完成！")
        return await bot.send(await convert_img(image))
    else:
        return await bot.send("你没有足够的积分！")
