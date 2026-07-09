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

from pydantic import BaseModel, ConfigDict

from gsuid_core.logger import logger
from gsuid_core.app_life import app

from .api import (
    get_models_by_task,
    build_model_catalog,
    build_backend_summary,
)

# ─────────────────────────── OpenAPI 响应模型 ───────────────────────────
# 这三个接口直接返回裸 dict(不套 {status,msg,data} 信封)。模型均开 extra="allow",
# 声明主要字段供 Apifox 展示,未声明字段原样透传(不过滤、不丢字段)。


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class ModelInfo(_Base):
    name: str
    display_name: str = ""
    task_type: str = ""
    backend: str = ""
    available: bool = True
    unavailable_reason: str | None = None
    point_cost: int = 0
    priority: int = 0
    description: str = ""
    input_schema: dict = {}
    output_schema: dict = {}


class ModelCatalog(_Base):
    """/models 与 /models/{task_type} 的返回。"""

    task_types: list[str] = []
    task_display: dict = {}
    total: int = 0
    available_count: int = 0
    models: list[ModelInfo] = []


class BackendInfo(_Base):
    name: str
    available: bool = True
    model_count: int = 0


class BackendSummary(_Base):
    """/models/summary 的返回。"""

    backends: list[BackendInfo] = []
    totals: dict = {}


# ═══════════════════════════════════════════════════════════════════════
#  FastAPI 路由 — 直接挂在框架 app 上
# ═══════════════════════════════════════════════════════════════════════


@app.get("/RH_ComfyUI/models", summary="列出全部模型", tags=["生成引擎/模型清单"], response_model=ModelCatalog)
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


@app.get(
    "/RH_ComfyUI/models/{task_type}",
    summary="按任务类型列出模型",
    tags=["生成引擎/模型清单"],
    response_model=ModelCatalog,
)
async def list_models_by_task(task_type: str) -> dict[str, object]:
    """按任务类型过滤

    `task_type` ∈ `image` / `video` / `music` / `speech`,亦接受中文别名
    (如 `图片` / `视频` / `音乐` / `语音` / `tts`)。
    """
    return await get_models_by_task(task_type, include_unavailable=True)


@app.get(
    "/RH_ComfyUI/models/summary",
    summary="模型可用性摘要",
    tags=["生成引擎/模型清单"],
    response_model=BackendSummary,
)
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
