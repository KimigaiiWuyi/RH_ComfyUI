"""RH_ComfyUI 模型清单 — Web API 路由模块.

通过复用框架的 FastAPI app([`gsuid_core.app_life.app`](../../../gsuid_core/app_life.py))
对外暴露三个模型查询接口。Bot 启动且 `core_config.ENABLE_HTTP=True`
时,以下路径立即在 `HOST:PORT` 上生效:

- `GET /RH_ComfyUI/models`           — 全量模型清单(按任务类型分组,含后端可用性)
- `GET /RH_ComfyUI/models/{task}`    — 按任务类型(image/video/music/speech)过滤
- `GET /RH_ComfyUI/models/summary`   — 后端可用性摘要(总览面板用)

把这些路由从 `__init__.py` 拆出来,避免触发器注册模块掺杂 Web 层代码;
`__init__.py` 仅 `import .webapi` 即可确保路由在启动时挂载到 FastAPI app。
"""

from __future__ import annotations

from gsuid_core.logger import logger
from gsuid_core.app_life import app

from .api import (
    get_models_by_task,
    build_model_catalog,
    build_backend_summary,
)

# ═══════════════════════════════════════════════════════════════════════
#  FastAPI 路由 — 直接挂在框架 app 上
# ═══════════════════════════════════════════════════════════════════════


@app.get("/RH_ComfyUI/models", tags=["RH_ComfyUI"])
async def list_all_models() -> dict[str, object]:
    """全量模型清单 — 按任务类型分组

    返回结构:
    ```
    {
      "task_types": ["image", "video", "music", "speech"],
      "task_display": {"image": "图片生成", ...},
      "total": 12,
      "available_count": 9,
      "models": [
        {
          "name": "qwen_2512",
          "display_name": "Qwen 2512",
          "task_type": "image",
          "backend": "rh_app",
          "available": true,
          "unavailable_reason": null,
          "point_cost": 2,
          "priority": 60,
          "description": "...",
          "input_schema": {...},
          "output_schema": {...}
        }
      ]
    }
    ```
    """
    return await build_model_catalog(include_unavailable=True)


@app.get("/RH_ComfyUI/models/{task_type}", tags=["RH_ComfyUI"])
async def list_models_by_task(task_type: str) -> dict[str, object]:
    """按任务类型过滤

    `task_type` ∈ `image` / `video` / `music` / `speech`,亦接受中文别名
    (如 `图片` / `视频` / `音乐` / `语音` / `tts`)。
    """
    return await get_models_by_task(task_type, include_unavailable=True)


@app.get("/RH_ComfyUI/models/summary", tags=["RH_ComfyUI"])
async def backend_summary() -> dict[str, object]:
    """后端可用性摘要 — 总览面板用

    ```
    {
        "backends": [{"name": "gpt-image-2", "available": true, "model_count": 3}, ...],
        "totals": {"backends": 6, "models": 12, "available_models": 9},
    }
    ```
    """
    return await build_backend_summary()


logger.info(
    "[rh_models] FastAPI 路由已注册: /RH_ComfyUI/models, /RH_ComfyUI/models/{task_type}, /RH_ComfyUI/models/summary"
)


__all__ = [
    "list_all_models",
    "list_models_by_task",
    "backend_summary",
]
