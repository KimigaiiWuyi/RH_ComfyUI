from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

from ..utils.wrapper import gen_music, gen_speech, check_point
from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG

Music_Point: int = RHCOMFYUI_CONFIG.get_config("Music_Point").data
Speech_Point: int = RHCOMFYUI_CONFIG.get_config("Speech_Point").data

sv_audio = SV("AI音频")


@sv_audio.on_command(("生成音乐", "音乐生成"))
async def _(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要生成的音乐prompt！")

    # 确认积分
    success, msg = await check_point(ev, Music_Point)
    if not success:
        return await bot.send(msg)
    else:
        await bot.send(msg)
        music = await gen_music(prompt)

        if music is None:
            return await bot.send("❌ 音乐生成失败！请检查prompt是否正确！")

        await bot.send("✅ 音乐生成完成！")
        return await bot.send(music)


@sv_audio.on_command(("生成语音", "语音生成"))
async def _(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要生成的语音文本！")

    # 确认积分
    success, msg = await check_point(ev, Speech_Point)
    if not success:
        return await bot.send(msg)
    else:
        await bot.send(msg)
        speech = await gen_speech(prompt)

        if speech is None:
            return await bot.send("❌ 语音生成失败！请检查prompt是否正确！")

        await bot.send("✅ 语音生成完成！")
        return await bot.send(speech)
