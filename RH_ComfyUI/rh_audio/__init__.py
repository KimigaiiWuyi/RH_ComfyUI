from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment

from ..utils.comfyui.wrapper import gen_music, gen_speech
from ..utils.database.models import RHBind

sv_audio = SV("AI音频")


@sv_audio.on_command(("生成音乐", "音乐生成"))
async def _(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要生成的音乐prompt！")

    if await RHBind.deduct_point(ev.user_id, ev.bot_id, 2):
        await bot.send("💪 积分充足！已扣除2点积分!\n✅ 正在生成音乐，预计将等待1分钟...")
        music = await gen_music(prompt)

        if music is None:
            return await bot.send("❌ 音乐生成失败！请检查prompt是否正确！")

        await bot.send("✅ 音乐生成完成！")
        return await bot.send(MessageSegment.record(music))
    else:
        return await bot.send("❌ 积分不足！无法生成音乐！")


@sv_audio.on_command(("生成语音", "语音生成"))
async def _(bot: Bot, ev: Event):
    prompt = ev.text.strip()

    if not prompt:
        return await bot.send("你需要在命令后面加入你要生成的语音文本！")

    if await RHBind.deduct_point(ev.user_id, ev.bot_id, 2):
        await bot.send("💪 积分充足！已扣除2点积分!\n✅ 正在生成语音，预计将等待1分钟...")
        speech = await gen_speech(prompt)

        if speech is None:
            return await bot.send("❌ 语音生成失败！请检查prompt是否正确！")

        await bot.send("✅ 语音生成完成！")
        return await bot.send(MessageSegment.record(speech))
    else:
        return await bot.send("❌ 积分不足！无法生成语音！")
