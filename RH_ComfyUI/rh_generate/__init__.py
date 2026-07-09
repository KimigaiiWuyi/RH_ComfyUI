"""统一 AI 生成命令模块 — 合并原 rh_draw/rh_video/rh_audio"""

from __future__ import annotations

from typing import Optional

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..core.base.errors import GenerationError
from ..utils.core.types import audio_ref
from ..utils.core.parser import parse_mood_from_prompt, parse_model_from_prompt
from ..utils.core.request import TaskType, GenerationResult, GenerationRequest
from ..core.billing.policy import BillingContext
from ..utils.image_process import flatten_transparent_to_white
from ..core.base.generation import AIGCGenerationBase
from ..core.dispatch.context import DispatchContext
from ..core.routing.registry import model_registry
from ..core.dispatch.dispatcher import dispatch
from ..core.billing.points_policy import PointsBillingPolicy

sv_gen = SV("AI生成")

_POINTS_POLICY = PointsBillingPolicy()


# ── 内部通用执行函数 ──


async def _do_generate(
    request: GenerationRequest,
    ev: Event,
    bot: Bot,
    *,
    on_progress=None,
) -> Optional[GenerationResult]:
    """通用生成执行流程 — 只负责协议翻译,路由/计费/退款/统计都在 dispatch() 里"""
    selected: dict[str, str] = {"display_name": "", "cost": "0"}

    async def _on_selected(model: AIGCGenerationBase, cost: int) -> None:
        selected["display_name"] = model.display_name
        selected["cost"] = str(cost)
        now_point = await _current_point(ev)
        await bot.send(
            f"💪 积分充足！已扣除{cost}积分!\n📋 当前积分: {now_point}\n"
            f"✅ 正在生成，预计将等待1分钟...\n🎯 使用模型: {model.display_name}"
        )

    async def _wrapped_progress(event) -> None:
        await bot.send(f"⏳ {event.message}")
        if on_progress is not None:
            try:
                await on_progress(event)
            except Exception:  # noqa: BLE001
                pass

    entry_point = "agent" if type(bot).__name__ == "MockBot" else "command"
    ctx = DispatchContext(
        billing=BillingContext(user_id=ev.user_id, bot_id=ev.bot_id, entry_point=entry_point),
        policy=_POINTS_POLICY,
        on_progress=_wrapped_progress,
        on_model_selected=_on_selected,
        group_id=ev.group_id or "",
    )

    try:
        result = await dispatch(request, ctx)
        ai_return(f"生成完成，使用模型: {result.model_used}")
        return result
    except GenerationError as e:
        ai_return(f"错误：{e.user_message}")
        await bot.send(f"❌ {e.user_message}")
        return None
    except Exception as e:
        logger.exception(f"[RHComfyUI] 生成失败: {e}")
        refund_msg = f"已退回{selected['cost']}积分。" if selected["display_name"] else ""
        ai_return(
            f"错误：生成失败 - {str(e)}。{refund_msg}"
            "不要在同一轮对话中盲目切换到未确认可用的云端 TTS 后端重试；"
            "如果错误码是 MiniMax 2038，表示账号未开通 voice_clone 权限，代码无法绕过。"
        )
        await bot.send(f"❌ 生成失败: {e}" + (f"\n↩️ {refund_msg}" if refund_msg else ""))
        return None


async def _current_point(ev: Event) -> int:
    from ..utils.database.models import RHBind

    return await RHBind.get_point(ev.user_id, ev.bot_id)


# ── 生图命令 ──


@sv_gen.on_command(
    "生图",
    block=True,
    to_ai="""根据文字描述生成图片;若附带参考图片则对其进行编辑/重绘。
    当用户想要创建图片、画图、生成插画、设计图等时调用。
    不提供图片即文生图;提供图片则按图片编辑处理(已不再有独立的潜空间图生图)。
    可通过模型名指定使用哪个模型，不指定则自动选择(系统按输入与可用性自动路由)。

    Args:
        text: 生成描述 + 可选模型名。
              格式1（自动选模型）: "一只可爱的猫咪在草地上玩耍"
              格式2（指定模型）: "qwen 一只可爱的猫咪"
              格式3（指定 Anima 二次元模型，必须使用小写模型名前缀）: "anima 蕾米莉亚的图"
              格式4（指定 MiniMax 模型）: "image01 一只可爱的猫咪"
              常用模型名: qwen, banana, banana_pro, anima, image01（即 minimax_image01）, gpt
              当用户明确要求 Anima / anima 模型，或需求是二次元、动漫角色、插画、萌系头像时，text 必须以 "anima " 开头。
              当用户明确要求 image01 / minimax 模型时，text 必须以 "image01 " 开头。
        image_id: 可选，参考图片的资源ID（img_xxxxxxxx），有则按编辑处理。
    """,
)
async def generate_image(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供生成描述")
        await bot.send("请在命令后输入描述，例如：\n生图 一只可爱的猫咪\n生图 qwen 一只可爱的猫咪")
        return

    # 统一为 IMAGE 模态:具体走文生图还是编辑,由路由按"有没有图片"决定
    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.IMAGE)

    request = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt=actual_prompt,
        model=model_name,
    )

    # 附加图片(有图则路由到编辑类节点)
    if ev.image_id:
        from gsuid_core.utils.resource_manager import RM

        image_bytes = await RM.get(ev.image_id)
        request.images = [flatten_transparent_to_white(image_bytes)]

    # 执行生成
    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    # 发送结果
    await bot.send("✅ 图片生成完成！")
    await bot.send(await convert_img(result.data))


# ── 改图命令 ──


@sv_gen.on_command(
    ("改图", "编辑图片", "图片编辑"),
    block=True,
    to_ai="""对已有图片进行编辑和修改，如局部修改、元素添加删除、风格转换。
    当用户想要修改图片、编辑照片、给图片加东西、去掉图片中的元素时调用。
    必须提供至少一张图片的资源ID。

    Args:
        text: 编辑指令 + 可选模型名。
              格式1: "把背景换成海边"
              格式2: "qwen 把背景换成海边"
        image_id: 必填，要编辑的图片资源ID（img_xxxxxxxx）。
    """,
)
async def edit_image(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供编辑指令")
        await bot.send("请在命令后输入编辑指令，例如：改图 把背景换成海边")
        return

    # 兼容 AI 通过 image_id 参数传入资源 ID 的情况
    image_id_list = ev.image_id_list or ([ev.image_id] if ev.image_id else [])
    if not image_id_list:
        ai_return("错误：编辑图片必须附带图片")
        await bot.send("编辑图片需要附带至少一张图片！")
        return

    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.IMAGE)

    from gsuid_core.utils.resource_manager import RM

    images = [flatten_transparent_to_white(await RM.get(img_id)) for img_id in image_id_list]

    request = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt=actual_prompt,
        model=model_name,
        images=images,
    )

    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    await bot.send("✅ 图片编辑完成！")
    await bot.send(await convert_img(result.data))


# ── 生视频命令 ──


@sv_gen.on_command(
    ("生视频", "生成视频"),
    block=True,
    to_ai="""根据文字/图片/音视频生成视频。当用户想要创建视频、让图片动起来时调用。
    统一为"视频"任务,具体形态由系统按输入自动决定,无需手动区分:
      - 不传图           → 文生视频
      - 传 1 张图         → 图生视频(该图作首帧)
      - 传 ≥2 张图        → 首尾帧生视频(图片1=首帧, 图片2=尾帧)
      - 传图+音/视频参考  → 多模态参考生视频(prompt 中用 "图片1/音频1" 等代号引用素材)

    Args:
        text: 视频描述 + 可选模型名。
              格式1: "一只猫在草地上追蝴蝶"
              格式2: "wan 一只猫在草地上追蝴蝶"
              格式3 (Seedance 多模态): "图片1为主角,音频1为背景音乐,他在草原上奔跑"
        image_id: 可选，参考图片的资源ID（img_xxxxxxxx），支持多张。
        audio_id: 可选,多模态参考音频。
    """,
)
async def generate_video(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供视频描述")
        await bot.send("请在命令后输入视频描述")
        return

    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.VIDEO)

    request = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=actual_prompt,
        model=model_name,
    )

    # 收集参考图片/音频;具体走 文生/图生/首尾帧/多模态 由路由+mapper 按输入分发
    image_ids = ev.image_id_list or ([ev.image_id] if ev.image_id else [])
    audio_ids = ev.audio_id_list or ([ev.audio_id] if ev.audio_id else [])
    if image_ids or audio_ids:
        from gsuid_core.utils.resource_manager import RM

        for img_id in image_ids:
            try:
                request.images.append(await RM.get(img_id))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[generate_video] 加载图片 {img_id} 失败: {e}")
        for audio_id in audio_ids:
            try:
                request.audio_refs.append(audio_ref(data=await RM.get(audio_id)))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[generate_video] 加载音频 {audio_id} 失败: {e}")

    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    await bot.send("✅ 视频生成完成！")
    await bot.send(MessageSegment.video(result.data))

    # ── 尾帧图(若 Seedance 返回) ──
    last_frame = (result.outputs or {}).get("last_frame")
    if last_frame:
        try:
            await bot.send("🎞️ 视频尾帧图：")
            await bot.send(await convert_img(last_frame))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[generate_video] 发送尾帧失败: {e}")


# ── 生音乐命令 ──


@sv_gen.on_command(
    ("生音乐", "生成音乐"),
    block=True,
    to_ai="""根据描述生成音乐。
    当用户想要生成背景音乐、创作音乐、制作配乐时调用。

    Args:
        text: 音乐描述 + 可选模型名。
              例如 "轻松愉快的背景音乐，适合咖啡厅"
    """,
)
async def generate_music(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供音乐描述")
        await bot.send("请在命令后输入音乐描述")
        return

    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.MUSIC)

    request = GenerationRequest(
        task_type=TaskType.MUSIC,
        prompt=actual_prompt,
        model=model_name,
    )

    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    await bot.send("✅ 音乐生成完成！")
    await bot.send(MessageSegment.record(result.data))


# ── 生语音命令 ──


@sv_gen.on_command(
    ("生语音", "生成语音"),
    block=True,
    to_ai="""将文字转换为自然语音，支持上传参考音频进行语音克隆，支持情绪控制。
    当用户想要文字转语音、制作有声内容、用特定音色朗读时调用。
    如果用户附带了音频文件，则使用该音频的音色进行语音克隆生成。

    **重要**：当没有用户提供参考音频时，你必须先调用
    `get_self_persona_info(info_type="audio", persona_name=<当前Persona名>)`
    获取自己Persona的音频资源ID，然后将返回的资源ID作为 audio_id 传入，
    这样生成的语音就会使用你自己的音色。
    如果 MiniMax 返回 2038 / voice clone user forbidden，表示账号未开通 MiniMax
    音色复刻权限，不能通过更换参数解决；不要继续用 MiniMax 重试。
    如果 MiMo 返回 401 / Invalid API Key，表示 MIMO_apikey 无效，不能继续调用 MiMo。

    可用模型（text 中的模型名前缀）：
    - IndexTTS2 本地模型（默认）：不加前缀，或前缀 "indextts"
    - MiniMax T2A 云端模型：前缀 "minimax"
    - 小米 MiMo TTS 云端模型：前缀 "mimo"（支持音色复刻、风格控制、方言、唱歌）

    Args:
        text: 要转换的文字 + 可选模型名 + 可选情绪标签。
              格式1: "欢迎使用 RH_ComfyUI"（使用默认 IndexTTS2）
              格式2: "minimax 欢迎使用 RH_ComfyUI"（使用 MiniMax）
              格式3: "mimo 欢迎使用 RH_ComfyUI"（使用小米 MiMo TTS）
              格式4: "minimax [happy] 欢迎使用 RH_ComfyUI"（MiniMax + 情绪）
              格式5: "mimo [用慵懒的语调] 我爱你"（MiMo + 风格指令）
              格式6: "mimo [情绪:东北话] 哎呀妈呀"（MiMo + 方言）
              情绪标签用方括号包裹，放在文本开头（模型名之后）。
              当用户提到"小米"、"MiMo"、"mimo"时，必须使用 "mimo" 前缀。
              当用户提到"minimax"时，使用 "minimax" 前缀。
        audio_id: 可选，参考音频的资源ID，有则进行语音克隆。
                  若未提供且无用户上传音频，应先调用 get_self_persona_info 获取自身音色。
    """,
)
async def generate_speech(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供要转换的文字")
        await bot.send("请在命令后输入要转换的文字")
        return

    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.SPEECH)

    # 从文本中解析情绪标签（格式: [情绪] 或 [情绪:xxx]）
    mood, actual_prompt = parse_mood_from_prompt(actual_prompt)

    request = GenerationRequest(
        task_type=TaskType.SPEECH,
        prompt=actual_prompt,
        model=model_name,
        mood=mood,
    )

    # 附加参考音频（语音克隆音色）：优先使用 audio_id（AI 调用或用户语音消息），
    # 其次检查用户上传的音频文件附件
    if ev.audio_id:
        from gsuid_core.utils.resource_manager import RM

        request.reference_audio = await RM.get(ev.audio_id)
    elif ev.file and ev.file_name and ev.file_name.lower().endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a")):
        import base64

        import httpx

        if ev.file_type == "url":
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(ev.file)
                resp.raise_for_status()
                request.reference_audio = resp.content
        elif ev.file_type == "base64":
            request.reference_audio = base64.b64decode(ev.file)

    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    await bot.send("✅ 语音生成完成！")
    await bot.send(MessageSegment.record(result.data))


# ── 模型信息命令 ──


@sv_gen.on_fullmatch("模型列表", block=True)
async def list_models(bot: Bot, ev: Event) -> None:
    """列出所有可用模型(数据源:model_registry,含外部注入模型)"""
    lines = ["📋 可用模型列表：\n"]
    for task_type in TaskType:
        models = model_registry.by_modality(task_type)
        if not models:
            continue

        type_names = {
            TaskType.IMAGE: "🖼️ 图片生成",
            TaskType.VIDEO: "🎬 视频生成",
            TaskType.MUSIC: "🎵 音乐生成",
            TaskType.SPEECH: "🗣️ 语音生成",
        }
        lines.append(f"\n{type_names.get(task_type, task_type.value)}：")

        for m in models:
            available = "✅" if await m.check_available() else "❌"
            lines.append(f"  {available} {m.name} — {m.display_name} ({m.point_cost}积分)")

    await bot.send("\n".join(lines))


@sv_gen.on_command("模型详情", block=True)
async def model_detail(bot: Bot, ev: Event) -> None:
    """查看模型详细信息"""
    model_name = ev.text.strip()
    model = model_registry.get(model_name)
    if not model:
        await bot.send(f"未找到模型: {model_name}，发送 模型列表 查看所有模型")
        return

    channels = "、".join(b.channel.name for b in model.channel_bindings())
    info = (
        f"📋 模型详情：{model.display_name}\n\n"
        f"🔹 名称: {model.name}\n"
        f"🔹 任务类型: {model.modality.value}\n"
        f"🔹 通道: {channels}\n"
        f"🔹 积分消耗: {model.point_cost}\n"
        f"🔹 描述: {model.card.description}\n\n"
        f"📖 详细说明:\n"
        f"{model.card.to_knowledge_text(name=model.name, display_name=model.display_name, point_cost=model.point_cost)}"
    )
    await bot.send(info)


@sv_gen.on_fullmatch("通道状态", block=True)
async def channel_status(bot: Bot, ev: Event) -> None:
    """展示各模型通道的熔断/失败计数(运维排障)"""
    from ..core.routing.balancer import get_default_balancer

    snapshot = get_default_balancer().health_snapshot()
    if not snapshot:
        await bot.send("📡 所有通道健康,无失败/熔断记录")
        return
    lines = ["📡 通道状态："]
    for key, state in snapshot.items():
        breaker = "🔴 熔断中" if state["circuit_open"] else "🟢 正常"
        lines.append(f"  {key}: 失败 {state['failure_count']} 次 {breaker}")
    await bot.send("\n".join(lines))
