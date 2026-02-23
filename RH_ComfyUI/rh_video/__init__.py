from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from ..utils.comfyui.wrapper import gen_video_by_img, gen_video_by_text
from ..utils.database.models import RHBind

sv_video = SV("AI视频")


@sv_video.on_command(("生成视频", "视频生成"))
async def _(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要生成的视频文本！")

    if await RHBind.deduct_point(ev.user_id, ev.bot_id, 8):
        await bot.send("💪 积分充足！已扣除8点积分!\n✅ 正在生成视频，预计将等待5分钟...")

        if ev.image_id:
            video = await gen_video_by_img(prompt, ev.image_id)
        else:
            video = await gen_video_by_text(prompt)

        if video is None:
            return await bot.send("❌ 视频生成失败！请检查prompt是否正确！")

        await bot.send("✅ 视频生成完成！")
        return await bot.send(video)
    else:
        return await bot.send("❌ 积分不足！无法生成视频！")
