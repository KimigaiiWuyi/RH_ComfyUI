"""RH_ComfyUI 模型清单 — Web API 路由模块.

通过复用框架的 FastAPI app([`gsuid_core.app_life.app`](../../../gsuid_core/app_life.py))
对外暴露四个模型查询接口。Bot 启动且 `core_config.ENABLE_HTTP=True`
时,以下路径立即在 `HOST:PORT` 上生效:

- `GET /api/RH_ComfyUI/models`           — 全量模型清单(按任务类型分组,含后端可用性)
- `GET /api/RH_ComfyUI/models/summary`   — 后端可用性摘要(总览面板用)
- `GET /api/RH_ComfyUI/models/estimate`  — 估算某模型在指定参数下的积分消耗
- `GET /api/RH_ComfyUI/models/{task}`    — 按任务类型(image/video/music/speech)过滤

注意:`summary` 和 `estimate` 必须注册在 `{task_type}` 之前 —— Starlette 按注册
顺序匹配,否则这两个字面路径会被当成 task_type 吃掉。

把这些路由从 `__init__.py` 拆出来,避免触发器注册模块掺杂 Web 层代码;
`__init__.py` 仅 `import .webapi` 即可确保路由在启动时挂载到 FastAPI app。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from gsuid_core.logger import logger
from gsuid_core.app_life import app

from .api import (
    get_models_by_task,
    build_model_catalog,
    build_backend_summary,
    estimate_model_points,
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
    # 取消能力(2026-08):本进程 cancel_generation / 上游 DELETE(通道级)
    # 缺省 False:未知/不完整条目 fail-closed
    execution_mode: str = "sync"
    supports_cancel: bool = False
    supports_remote_cancel: bool = False
    # channels:[{name, vendor_model, available, supports_cancel, supports_remote_cancel}]
    channels: list[dict] = []


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


class EstimateResult(_Base):
    """/models/estimate 的返回。"""

    model: str
    point_cost: int = 0
    is_dynamic: bool = False
    params: dict = {}


# ═══════════════════════════════════════════════════════════════════════
#  FastAPI 路由 — 直接挂在框架 app 上
# ═══════════════════════════════════════════════════════════════════════


@app.get("/api/RH_ComfyUI/models", summary="列出全部模型", tags=["生成引擎/模型清单"], response_model=ModelCatalog)
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
          "output_schema": {...},
          "execution_mode": "sync|async_poll",
          "supports_cancel": true,
          "supports_remote_cancel": false,
          "channels": [
            {"name": "ark", "vendor_model": "...", "available": true,
             "supports_cancel": true, "supports_remote_cancel": true}
          ]
        }
      ]
    }
    ```

    取消字段(引擎与调用方统一契约 — 调用方**必须**据此决定是否展示取消按钮):
    - ``supports_cancel``: 模型顶层;本进程 ``POST .../tasks/cancel`` 是否允许
      (有 channels 时 = 任一通道 ``channels[].supports_cancel`` 的 OR)
    - ``supports_remote_cancel``: 创建上游后是否可远程 DELETE
      (有 channels 时 = 通道 OR;精确路由读当前通道)
    - ``channels[]``: 多通道时以**当前选用通道**的 ``supports_*`` 为准
    - ``rh_app``: 顶层与通道均为 false(只能 resume,不能 cancel)
    - 引擎 cancel 入口: ``POST /api/RH_ComfyUI/tasks/cancel``(与上述标志一致)
    """
    return await build_model_catalog(include_unavailable=True)


# /models/summary 与 /models/estimate 都必须在 /models/{task_type} 之前注册
# —— Starlette 按注册顺序匹配,否则它们会被当成 task_type 吃掉。
@app.get(
    "/api/RH_ComfyUI/models/summary",
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


@app.get(
    "/api/RH_ComfyUI/models/estimate",
    summary="估算模型积分消耗(动态)",
    tags=["生成引擎/模型清单"],
    response_model=EstimateResult,
)
async def estimate_model_cost(
    model: str,
    ratio: Optional[str] = None,
    image_size: Optional[str] = None,
    quality: Optional[str] = None,
    resolution: Optional[str] = None,
    duration: Optional[int] = None,
    generate_audio: Optional[bool] = None,
    num_input_images: int = 0,
    num_video_refs: int = 0,
    input_video_duration: Optional[float] = None,
) -> dict[str, object]:
    """根据用户实时选择的参数,估算某模型消耗的积分。

    调用方在用户切换 ratio / image_size / quality / resolution / duration / 已连输入数量时调用,
    实时预览扣费。

    例(图片模型):
      ``GET /models/estimate?model=gpt-image-2&ratio=1:1&image_size=4K&quality=high&num_input_images=3``
    例(视频模型):
      ``GET /models/estimate?model=seedance2&resolution=1080p&duration=10&num_video_refs=1``
    例(带输入视频真实时长):
      ``GET /models/estimate?model=seedance2.5&resolution=720p&duration=5&num_video_refs=1&input_video_duration=12.5``

    num_input_images / num_video_refs 为已连输入数量(0=文生)。后端用占位 bytes/对象表示,
    estimate_cost 只取 len()/bool 不读内容,不会触发真实媒体下载。
    input_video_duration 为输入参考视频总时长(秒),用于 Seedance token 公式中的
    「输入视频时长」;未传时按每段默认 5 秒 × 段数估算。

    对未覆盖 ``estimate_cost()`` 的模型,返回其静态 point_cost。
    """
    return await estimate_model_points(
        model_name=model,
        ratio=ratio,
        image_size=image_size,
        quality=quality,
        resolution=resolution,
        duration=duration,
        generate_audio=generate_audio,
        num_input_images=num_input_images,
        num_video_refs=num_video_refs,
        input_video_duration=input_video_duration,
    )


class CancelTaskBody(_Base):
    """取消进行中任务请求体。"""

    trace_id: Optional[str] = None
    record_id: Optional[int] = None
    reason: str = "user_cancel"


class CancelTaskResult(_Base):
    """取消任务结果。"""

    ok: bool = False
    found: bool = False
    cancelled_local: bool = False
    cancelled_remote: bool = False
    model: str = ""
    vendor_task_id: str = ""
    message: str = ""


@app.post(
    "/api/RH_ComfyUI/tasks/cancel",
    summary="取消进行中的生成任务",
    tags=["生成引擎/任务"],
    response_model=CancelTaskResult,
)
async def cancel_running_task(body: CancelTaskBody) -> dict[str, object]:
    """取消本进程内进行中的生成。

    调用方应先读 ``GET /api/RH_ComfyUI/models`` 的 ``supports_cancel``
    (或多通道时当前通道的 ``channels[].supports_cancel``);为 false 时
    **不要**调本接口(如 rh_app 一律不可取消,只能 resume)。

    用 ``trace_id``(推荐,与提交时相同)或 ``record_id``(统计表主键)定位。
    支持 remote 的通道会先 DELETE 上游,再中断本地 Task;
    dispatch 记 ``status=cancelled`` 并退款。``supports_cancel=false``
    的进行中任务会返回 ok=false(与目录能力一致)。

    例::

        POST / api / RH_ComfyUI / tasks / cancel
        {"trace_id": "job-or-trace-abc-123"}
    """
    from ..core.dispatch.active_tasks import cancel_generation

    if not body.trace_id and body.record_id is None:
        return {
            "ok": False,
            "found": False,
            "cancelled_local": False,
            "cancelled_remote": False,
            "model": "",
            "vendor_task_id": "",
            "message": "必须提供 trace_id 或 record_id",
        }
    result = await cancel_generation(
        trace_id=body.trace_id,
        record_id=body.record_id,
        reason=body.reason or "user_cancel",
    )
    logger.info(
        f"[webapi] cancel task: trace_id={body.trace_id}, record_id={body.record_id}, "
        f"ok={result.get('ok')}, message={result.get('message')}"
    )
    return result


@app.get(
    "/api/RH_ComfyUI/models/{task_type}",
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


logger.info(
    "[rh_models] FastAPI 路由已注册: /api/RH_ComfyUI/models, "
    "/api/RH_ComfyUI/models/summary, /api/RH_ComfyUI/models/{task_type}, "
    "/api/RH_ComfyUI/models/estimate"
)


__all__ = [
    "list_all_models",
    "list_models_by_task",
    "backend_summary",
    "estimate_model_cost",
]
