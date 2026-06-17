"""RH_ComfyUI 模型清单 — 命令模块.

职责:只负责定义 SV 与命令触发器,把 `bot.send` 文本命令挂到机器人。

数据层在 [`api.py`](api.py)、文本格式化与 AI 工具在 [`utils.py`](utils.py)、
Web API 路由在 [`webapi.py`](webapi.py)。本文件通过 `from . import webapi`
确保 FastAPI 路由在插件加载阶段挂载到框架 app。
"""

from __future__ import annotations

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event

# 触发 webapi 模块的副作用:把 @app.get(...) 路由挂到框架 app
from . import webapi  # noqa: F401
from .api import build_model_catalog
from .utils import format_text

# ═══════════════════════════════════════════════════════════════════════
#  机器人命令 — 触发即返回文本清单
# ═══════════════════════════════════════════════════════════════════════


sv_models = SV("模型清单")


@sv_models.on_command(("模型列表", "模型清单", "可用模型"), block=True)
async def cmd_list_models(bot: Bot, ev: Event):
    """查看当前所有可用模型清单(分任务类型展示)

    命令格式:
        模型列表                 — 全量
        模型列表 image           — 仅图片模态
        模型列表 图片 / 视频 / 音乐 / 语音
    """
    task_filter_raw = ev.text.strip()
    catalog = await build_model_catalog(include_unavailable=True, task_type=task_filter_raw or None)
    text = format_text(catalog, task_filter_raw or None)
    await bot.send(text)


__all__ = [
    "sv_models",
    "cmd_list_models",
]
