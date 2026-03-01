from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from ..utils.wrapper import check_point, gen_video_by_img, gen_video_by_text
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

Video_Point: int = RHCOMFYUI_CONFIG.get_config("Video_Point").data

sv_video = SV("AI视频")


@sv_video.on_command(("生成视频", "视频生成"))
async def _(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要生成的视频文本！")

    # 确认积分
    success, msg = await check_point(ev, Video_Point)
    if not success:
        return await bot.send(msg)
    else:
        await bot.send(msg)

        if ev.image_id:
            video = await gen_video_by_img(prompt, ev.image_id)
        else:
            video = await gen_video_by_text(prompt)

        if video is None:
            return await bot.send("❌ 视频生成失败！请检查prompt是否正确！")

        await bot.send("✅ 视频生成完成！")
        return await bot.send(video)
