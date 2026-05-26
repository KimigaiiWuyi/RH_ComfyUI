"""统一 AI 生成命令模块 — 合并原 rh_draw/rh_video/rh_audio"""

from __future__ import annotations

import io
from typing import Optional

from PIL import Image

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.ai_core.trigger_bridge import ai_return

from ..utils.points import check_point
from ..utils.core.parser import parse_model_from_prompt
from ..utils.core.router import ModelUnavailableError, route
from ..utils.core.request import TaskType, GenerationResult, GenerationRequest
from ..utils.core.executor import execute_generation
from ..utils.core.pipeline import pipeline_registry

sv_gen = SV("AI生成")


def _has_transparency(image: Image.Image) -> bool:
    """判断图片是否包含透明通道。"""
    if image.mode in ("RGBA", "LA"):
        alpha = image.getchannel("A")
        transparent_mask = alpha.point(lambda value: 255 - value)
        return transparent_mask.getbbox() is not None
    return image.mode == "P" and "transparency" in image.info


def _flatten_transparent_image_to_white(image_bytes: bytes) -> bytes:
    """将透明图片合成到白色背景，非透明图片保持原始字节。"""
    image = Image.open(io.BytesIO(image_bytes))
    if not _has_transparency(image):
        return image_bytes

    rgba_image = image.convert("RGBA")
    background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
    background.alpha_composite(rgba_image)

    output = io.BytesIO()
    background.convert("RGB").save(output, format="PNG")
    return output.getvalue()


# ── 内部通用执行函数 ──


async def _do_generate(
    request: GenerationRequest,
    ev: Event,
    bot: Bot,
) -> Optional[GenerationResult]:
    """通用生成执行流程：路由 → 积分检查 → 执行"""
    # 1. 路由
    try:
        pipeline = await route(request)
    except ModelUnavailableError as e:
        ai_return(f"错误：{e.reason}")
        await bot.send(f"❌ {e.reason}")
        return None

    # 2. 积分检查
    success, msg = await check_point(ev, pipeline.point_cost)
    if not success:
        ai_return(f"错误：积分不足，需要{pipeline.point_cost}积分")
        await bot.send(msg)
        return None
    await bot.send(f"{msg}\n🎯 使用模型: {pipeline.display_name}")

    # 3. 执行
    try:
        result = await execute_generation(request, pipeline)
        ai_return(f"生成完成，使用模型: {pipeline.display_name}")
        return result
    except Exception as e:
        logger.exception(f"[RHComfyUI] 生成失败: {e}")
        ai_return(f"错误：生成失败 - {str(e)}")
        await bot.send(f"❌ 生成失败: {e}")
        return None


# ── 生图命令 ──


@sv_gen.on_command(
    "生图",
    block=True,
    to_ai="""根据文字描述生成图片，或基于已有图片生成新图片。
    当用户想要创建图片、画图、生成插画、设计图等时调用。
    如果用户提供了参考图片，则进行图生图；否则进行文生图。
    可通过模型名指定使用哪个模型，不指定则自动选择。

    Args:
        text: 生成描述 + 可选模型名。
              格式1（自动选模型）: "一只可爱的猫咪在草地上玩耍"
              格式2（指定模型）: "qwen 一只可爱的猫咪"
              格式3（指定 Anima 二次元模型，必须使用小写模型名前缀）: "anima 蕾米莉亚的图"
              常用模型名: qwen, banana, banana_pro, anima
              当用户明确要求 Anima / anima 模型，或需求是二次元、动漫角色、插画、萌系头像时，text 必须以 "anima " 开头。
        image_id: 可选，参考图片的资源ID（img_xxxxxxxx），有则为图生图。
    """,
)
async def generate_image(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供生成描述")
        return await bot.send("请在命令后输入描述，例如：\n生图 一只可爱的猫咪\n生图 qwen 一只可爱的猫咪")

    # 自动推断任务类型
    has_image = bool(ev.image_id)
    task_type = TaskType.IMAGE2IMAGE if has_image else TaskType.TEXT2IMAGE

    # 解析可选模型名
    model_name, actual_prompt = parse_model_from_prompt(prompt, task_type)

    # 构建请求
    request = GenerationRequest(
        task_type=task_type,
        prompt=actual_prompt,
        model=model_name,
    )

    # 附加图片
    if has_image and ev.image_id:
        from gsuid_core.utils.resource_manager import RM

        image_bytes = await RM.get(ev.image_id)
        request.images = [image_bytes]

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
        return await bot.send("请在命令后输入编辑指令，例如：改图 把背景换成海边")

    # 兼容 AI 通过 image_id 参数传入资源 ID 的情况
    image_id_list = ev.image_id_list or ([ev.image_id] if ev.image_id else [])
    if not image_id_list:
        ai_return("错误：编辑图片必须附带图片")
        return await bot.send("编辑图片需要附带至少一张图片！")

    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.IMAGE_EDIT)

    from gsuid_core.utils.resource_manager import RM

    images = [_flatten_transparent_image_to_white(await RM.get(img_id)) for img_id in image_id_list]

    request = GenerationRequest(
        task_type=TaskType.IMAGE_EDIT,
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
    to_ai="""根据文字描述生成视频，或基于图片生成视频。
    当用户想要创建视频、让图片动起来时调用。
    如果用户提供了参考图片，则进行图生视频；否则进行文生视频。

    Args:
        text: 视频描述 + 可选模型名。
              格式1: "一只猫在草地上追蝴蝶"
              格式2: "wan 一只猫在草地上追蝴蝶"
        image_id: 可选，参考图片的资源ID（img_xxxxxxxx），有则为图生视频。
    """,
)
async def generate_video(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供视频描述")
        return await bot.send("请在命令后输入视频描述")

    has_image = bool(ev.image_id)
    task_type = TaskType.IMAGE2VIDEO if has_image else TaskType.TEXT2VIDEO

    model_name, actual_prompt = parse_model_from_prompt(prompt, task_type)

    request = GenerationRequest(
        task_type=task_type,
        prompt=actual_prompt,
        model=model_name,
    )

    if has_image and ev.image_id:
        from gsuid_core.utils.resource_manager import RM

        image_bytes = await RM.get(ev.image_id)
        request.images = [image_bytes]

    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    await bot.send("✅ 视频生成完成！")
    await bot.send(MessageSegment.video(result.data))


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
        return await bot.send("请在命令后输入音乐描述")

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
    to_ai="""将文字转换为自然语音。
    当用户想要文字转语音、制作有声内容时调用。

    Args:
        text: 要转换的文字 + 可选模型名。
              例如 "欢迎使用 RH_ComfyUI"
    """,
)
async def generate_speech(bot: Bot, ev: Event) -> None:
    prompt = ev.text.strip()
    if not prompt:
        ai_return("错误：未提供要转换的文字")
        return await bot.send("请在命令后输入要转换的文字")

    model_name, actual_prompt = parse_model_from_prompt(prompt, TaskType.SPEECH)

    request = GenerationRequest(
        task_type=TaskType.SPEECH,
        prompt=actual_prompt,
        model=model_name,
    )

    result = await _do_generate(request, ev, bot)
    if result is None:
        return

    await bot.send("✅ 语音生成完成！")
    await bot.send(MessageSegment.record(result.data))


# ── 模型信息命令 ──


@sv_gen.on_fullmatch("模型列表", block=True)
async def list_models(bot: Bot, ev: Event) -> None:
    """列出所有可用模型"""
    from ..utils.backends import backend_registry

    lines = ["📋 可用模型列表：\n"]
    for task_type in TaskType:
        pipelines = pipeline_registry.get_by_task(task_type)
        if not pipelines:
            continue

        type_names = {
            TaskType.TEXT2IMAGE: "🖼️ 文生图",
            TaskType.IMAGE2IMAGE: "🎨 图生图",
            TaskType.IMAGE_EDIT: "✏️ 图片编辑",
            TaskType.TEXT2VIDEO: "🎬 文生视频",
            TaskType.IMAGE2VIDEO: "🎥 图生视频",
            TaskType.MUSIC: "🎵 音乐生成",
            TaskType.SPEECH: "🗣️ 语音生成",
        }
        lines.append(f"\n{type_names.get(task_type, task_type.value)}：")

        for p in pipelines:
            backend = backend_registry.get(p.backend)
            available = "✅" if backend and await backend.check_available() else "❌"
            lines.append(f"  {available} {p.name} — {p.display_name} ({p.point_cost}积分)")

    await bot.send("\n".join(lines))


@sv_gen.on_command("模型详情", block=True)
async def model_detail(bot: Bot, ev: Event) -> None:
    """查看模型详细信息"""
    model_name = ev.text.strip()
    pipeline = pipeline_registry.get(model_name)
    if not pipeline:
        return await bot.send(f"未找到模型: {model_name}，发送 模型列表 查看所有模型")

    info = (
        f"📋 模型详情：{pipeline.display_name}\n\n"
        f"🔹 名称: {pipeline.name}\n"
        f"🔹 任务类型: {pipeline.task_type.value}\n"
        f"🔹 后端: {pipeline.backend}\n"
        f"🔹 积分消耗: {pipeline.point_cost}\n"
        f"🔹 描述: {pipeline.description}\n\n"
        f"📖 详细说明:\n{pipeline.knowledge_content}"
    )
    await bot.send(info)
