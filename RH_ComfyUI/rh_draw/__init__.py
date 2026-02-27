from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.utils.image.convert import convert_img

from ..utils.wrapper import gen_image_by_img, gen_image_by_text, gen_edit_img_by_img
from ..utils.database.models import RHBind
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

Draw_Point: int = RHCOMFYUI_CONFIG.get_config("Draw_Point").data
Edit_Image_Point: int = RHCOMFYUI_CONFIG.get_config("Edit_Image_Point").data

sv_draw = SV("AI绘图")


@sv_draw.on_command(("生图",), block=True)
async def draw_img(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要绘图的prompt！")

    if await RHBind.deduct_point(ev.user_id, ev.bot_id, Draw_Point):
        await bot.send(f"💪 积分充足！已扣除{Draw_Point}点积分!\n✅ 正在生成图片，预计将等待1分钟...")

        if ev.image_id:
            image = await gen_image_by_img(prompt, ev.image_id)
        else:
            image = await gen_image_by_text(prompt)

        await bot.send("✅ 图片生成完成！")
        return await bot.send(await convert_img(image))
    else:
        return await bot.send("你没有足够的积分！")


@sv_draw.on_command(("编辑图片", "图片编辑"), block=True)
async def edit_img_by_img(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要绘图的prompt！")

    if not ev.image_id_list:
        return await bot.send("编辑图片需要在命令后面加入至少一张图片！")

    if await RHBind.deduct_point(ev.user_id, ev.bot_id, Edit_Image_Point):
        await bot.send(f"💪 积分充足！已扣除{Edit_Image_Point}点积分!\n✅ 正在生成图片，预计将等待1分钟...")
        image = await gen_edit_img_by_img(prompt, ev.image_id_list)

        await bot.send("✅ 图片生成完成！")
        return await bot.send(await convert_img(image))
    else:
        return await bot.send("你没有足够的积分！")
