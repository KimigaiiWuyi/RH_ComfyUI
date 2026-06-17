"""Seedance 2.0 多模态视频生成 Mapper

核心职责:把 GenerationRequest 中的 prompt/images/video_refs/audio_refs/
ordered_content 翻译成 Seedance API 期望的有序 content 数组。

覆盖的场景:
- text2video            : [text]
- image2video           : [text, image(first_frame)]
- first_last_frame2video: [text, image(first_frame), image(last_frame)]
- multimodal2video      : [text, image, video, audio, ...] 任意顺序
- video_edit            : [text, video(待编辑), image(参考)]

约定:
- 所有 MediaRef 在必要时会自动转为 base64 data URL
  (Seedance 2.0 文档允许 URL / Base64 / 素材 ID 三种传入方式)
- 如果用户提供的是 URL,优先用 URL(更省带宽);否则用 base64
- ordered_content 优先于根据通用字段构造的隐式内容
- model ID 由 Adapter 在调用 mapper 前注入到 request.params["model"]
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ..core.types import (
    MediaRef,
    NodeOutput,
    ContentItem,
    ProgressEvent,
    image_ref,
    text_item,
    audio_item,
    image_item,
    video_item,
)
from ..core.request import GenerationRequest
from ..backends.seedance.api import SeedanceAPI


def _media_to_url_or_data(media: MediaRef, api: SeedanceAPI) -> Optional[str]:
    """将 MediaRef 转成 Seedance 可消费的 URL 或 base64 data URL"""
    if media.url:
        return media.url
    if media.data is None:
        return None
    mime = media.mime_type or "application/octet-stream"
    return api.bytes_to_data_url(media.data, mime)


def _build_content(
    request: GenerationRequest,
    api: SeedanceAPI,
    *,
    force_text_first: bool = True,
) -> list[dict[str, Any]]:
    """构造 Seedance content 数组"""
    # 优先用显式 ordered_content
    if request.ordered_content:
        items: list[ContentItem] = list(request.ordered_content)
    else:
        items = request.build_ordered_content(prepend_text=force_text_first)

    if not items:
        items = [text_item(request.prompt or "")]

    content: list[dict[str, Any]] = []
    for item in items:
        if item.type.value == "text":
            content.append({"type": "text", "text": item.text or ""})
            continue

        url = _media_to_url_or_data(item.media, api) if item.media else None
        if not url:
            continue

        d: dict[str, Any]
        if item.type.value == "image_url":
            d = {"type": "image_url", "image_url": {"url": url}}
        elif item.type.value == "video_url":
            d = {"type": "video_url", "video_url": {"url": url}}
        elif item.type.value == "audio_url":
            d = {"type": "audio_url", "audio_url": {"url": url}}
        else:
            continue

        if item.role:
            d["role"] = item.role
        content.append(d)

    return content


async def _execute_seedance(
    request: GenerationRequest,
    api: SeedanceAPI,
    *,
    mode: str = "video",
    on_progress=None,
) -> NodeOutput:
    """Seedance 通用执行流程

    1. 解析 backend_model(由 Adapter 注入到 request.params["model"])
    2. 构造 content
    3. create_task
    4. 轮询直到完成
    5. 下载 video_url + 可选尾帧图
    6. 封装为 NodeOutput
    """
    model = request.params.get("model") or "doubao-seedance-2-0-260128"

    content = _build_content(request, api)

    # ── 提交任务 ──
    if on_progress:
        await _safe_emit(on_progress, _evt("submitting", 5, "提交 Seedance 任务"))

    resp = await api.create_task(
        model=model,
        content=content,
        ratio=request.ratio,
        duration=request.duration if request.duration != 5 else None,
        resolution=request.resolution,
        seed=request.seed,
        generate_audio=request.generate_audio or None,
        watermark=None if request.watermark else False,
        camera_fixed=request.camera_fixed or None,
        return_last_frame=request.return_last_frame or None,
        service_tier=request.service_tier if request.service_tier != "default" else None,
        draft=request.params.get("draft"),
        callback_url=request.params.get("callback_url"),
    )
    task_id = resp.get("id")
    if not task_id:
        raise RuntimeError(f"Seedance 未返回 task id: {resp}")

    if on_progress:
        await _safe_emit(on_progress, _evt("queued", 15, f"任务已创建: {task_id}"))

    # ── 轮询 ──
    async def _poll_progress(task: dict[str, Any]) -> None:
        status = task.get("status")
        if status == SeedanceAPI.STATUS_QUEUED:
            await _safe_emit(on_progress, _evt("queued", 25, "排队中"))
        elif status == SeedanceAPI.STATUS_RUNNING:
            await _safe_emit(on_progress, _evt("running", 50, "Seedance 生成中"))

    final_task = await api.poll_until_done(
        task_id,
        interval_seconds=float(request.params.get("poll_interval", 8.0)),
        max_wait_seconds=float(request.params.get("max_wait_seconds", 1800.0)),
        on_progress=_poll_progress,
    )

    if on_progress:
        await _safe_emit(on_progress, _evt("downloading", 90, "下载视频"))

    # ── 解析结果 ──
    content_obj = final_task.get("content") or {}
    video_url = content_obj.get("video_url")
    if not video_url:
        raise RuntimeError(f"Seedance 任务成功但未返回 video_url: {final_task}")

    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        video_resp = await client.get(video_url)
        video_resp.raise_for_status()
        video_bytes = video_resp.content

    # outputs 仅存放附加产物(尾帧图);主视频已在 NodeOutput.data,避免双写
    outputs: dict[str, Any] = {}

    last_frame_url = content_obj.get("last_frame_url")
    if last_frame_url:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                lf_resp = await client.get(last_frame_url)
                lf_resp.raise_for_status()
                outputs["last_frame"] = lf_resp.content
        except Exception as e:  # noqa: BLE001
            from gsuid_core.logger import logger

            logger.warning(f"[Seedance] 下载尾帧失败: {e}")

    usage = final_task.get("usage") or {}
    resolution = final_task.get("resolution", "720p")
    ratio = final_task.get("ratio", request.ratio or "16:9")
    duration = final_task.get("duration", request.duration)
    fps = final_task.get("framespersecond", 24)
    seed_used = final_task.get("seed")

    if on_progress:
        await _safe_emit(on_progress, _evt("done", 100, "Seedance 完成"))

    return NodeOutput(
        status="ok",
        output_type="video",
        data=video_bytes,
        mime_type="video/mp4",
        outputs=outputs,
        usage={
            **usage,
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration,
            "fps": fps,
            "seed": seed_used,
            "task_id": task_id,
            "model": model,
        },
        raw=final_task,
        metadata={
            "task_id": task_id,
            "model": model,
            "resolution": resolution,
            "ratio": ratio,
            "duration": duration,
            "fps": fps,
            "seed": seed_used,
            "mode": mode,
        },
    )


# ═══════════════════════════════════════════════════════════════════════
#  暴露给 YAML mapper_func 的薄包装
# ═══════════════════════════════════════════════════════════════════════


async def seedance_video_mapper(
    request: GenerationRequest,
    api: SeedanceAPI,
    *,
    on_progress=None,
) -> NodeOutput:
    """统一视频 mapper —— 按输入形状自动分发,无需上层强制区分子任务

    分发规则(与路由的"输入档案"理念一致):
      - 已有显式 ordered_content        → 直接使用
      - 带音/视频参考                    → 多模态(图片/音视频均标 reference)
      - >=2 张图                         → 首尾帧(图1=首帧, 图2=尾帧, 其余 reference)
      - 1 张图                           → 图生视频(图1=首帧)
      - 0 张图                           → 文生视频
    """
    if request.ordered_content:
        return await _execute_seedance(request, api, mode="ordered", on_progress=on_progress)

    has_av = bool(request.audio_refs or request.video_refs)
    n_img = len(request.images)

    if has_av:
        items: list[ContentItem] = []
        if request.prompt:
            items.append(text_item(request.prompt))
        for img in request.images:
            items.append(image_item(image_ref(data=img), role="reference"))
        for v in request.video_refs:
            items.append(video_item(v, role="reference"))
        for a in request.audio_refs:
            items.append(audio_item(a, role="reference"))
        request.ordered_content = items
        return await _execute_seedance(request, api, mode="multimodal", on_progress=on_progress)

    if n_img >= 2:
        items = []
        if request.prompt:
            items.append(text_item(request.prompt))
        items.append(image_item(image_ref(data=request.images[0]), role="first_frame"))
        items.append(image_item(image_ref(data=request.images[1]), role="last_frame"))
        for img in request.images[2:]:
            items.append(image_item(image_ref(data=img), role="reference"))
        request.ordered_content = items
        return await _execute_seedance(request, api, mode="first_last_frame", on_progress=on_progress)

    if n_img == 1:
        items = []
        if request.prompt:
            items.append(text_item(request.prompt))
        items.append(image_item(image_ref(data=request.images[0]), role="first_frame"))
        request.ordered_content = items
        return await _execute_seedance(request, api, mode="image2video", on_progress=on_progress)

    return await _execute_seedance(request, api, mode="text2video", on_progress=on_progress)


# 注:
# - 纯文生视频与多模态/图生均统一走本 mapper,
#   上面根据 ``request.images / video_refs / audio_refs / ordered_content``
#   自动选择。
# - 样片(Draft)模式不再使用专属 mapper,改为调用方在调用前
#   设置 ``request.params["draft"] = True``(见 _execute_seedance
#   中的 `draft=...` 参数)。一般由调用方(如 AI agent
#   推荐的低成本预览)决定是否启用。
# - _execute_seedance 会读取 ``request.params.get("draft")`` 并
#   透传给 Seedance API,生成元数据中的 mode 也会以
#   "draft" 记录。


# ── 辅助 ──


def _evt(stage: str, percent: float, message: str) -> ProgressEvent:
    return ProgressEvent(stage=stage, percent=percent, message=message)


async def _safe_emit(cb, event) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass
