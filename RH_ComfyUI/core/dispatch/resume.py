"""resume_poll — 进程重启后按上游 task_id 继续轮询到出结果

不走 dispatch/计费(调用方须已自行预扣):只 poll + 下载产物 + 可选更新
RHComfyuiTaskRecord 终态。

支持后端(有 vendor_task_id 时):
  - seedance (ark / runninghub / 网关通道)
  - happyhorse (dashscope 等)
  - rh_app (AI 应用 query — **可 resume,但无 remote cancel**)
  - comfyui (local history / RH 工作流代理 history)
  - gemini-image (background interactions)

注意:**rh_app ≠ comfyui**
  - ``rh_app``: RunningHub ``/openapi/v2/run/ai-app`` ,无官方 cancel
  - ``comfyui``: 本地 Comfy 或 RH ``/prompt`` 工作流代理,可
    ``/task/openapi/cancel`` 或 local interrupt
  即便共用 ``RH_apikey``,backend / channel 名不同,能力也不同。
"""

from __future__ import annotations

import time
import asyncio
from typing import Any, Optional, Protocol, runtime_checkable

import httpx

from gsuid_core.logger import logger

from ...api import GenerationResult
from ..channels.channel import ProviderChannel
from ...utils.core.types import ProgressEvent, ProgressCallback


class ResumeNotSupportedError(RuntimeError):
    """该 backend/channel 无法仅凭 vendor_task_id 恢复轮询。"""


class ResumeFailedError(RuntimeError):
    """上游终态失败或产物不可用。"""


class ResumeCancelledError(ResumeFailedError):
    """上游任务已取消(非本进程 user cancel 的 CancelledError)。"""


@runtime_checkable
class _RemoteCancelCapable(Protocol):
    def supports_remote_cancel(self) -> bool: ...


@runtime_checkable
class _HasDelete(Protocol):
    async def delete(self, task_id: str) -> None: ...


@runtime_checkable
class _HasDeleteTask(Protocol):
    async def delete_task(self, task_id: str) -> None: ...


@runtime_checkable
class _HasPollUntilDone(Protocol):
    def poll_until_done(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class _HasApiKey(Protocol):
    api_key: str


@runtime_checkable
class _LegacyResumeClientHost(Protocol):
    def get_resume_client(self) -> object | None: ...


def _update_claimed_rows(result: object) -> bool:
    """判断 SQLAlchemy / 测试 double 的 UPDATE 是否抢到行。"""
    from sqlalchemy.engine import CursorResult

    if isinstance(result, CursorResult):
        return int(result.rowcount or 0) > 0
    # 测试 double:显式 __getattribute__,避免 getattr 默认值吞错
    try:
        rowcount = object.__getattribute__(result, "rowcount")
    except AttributeError:
        logger.warning(f"[resume_poll] finalize 无 rowcount: {type(result)!r}")
        return False
    if isinstance(rowcount, int):
        return rowcount > 0
    logger.warning(f"[resume_poll] finalize 无 rowcount: {type(result)!r}")
    return False


def _provider_supports_remote_cancel(provider: object | None) -> bool:
    """fail-closed:缺方法或抛错 → 不挂 remote cancel。"""
    if provider is None:
        return False
    if not isinstance(provider, _RemoteCancelCapable):
        return False
    try:
        return bool(provider.supports_remote_cancel())
    except Exception:  # noqa: BLE001
        return False


def _node_backend(model_obj: Any) -> str:
    """从模型对象读取 node.backend(无则空串)。"""
    try:
        node = model_obj.node
    except AttributeError:
        return ""
    if node is None:
        return ""
    try:
        backend = node.backend
    except AttributeError:
        return ""
    return str(backend or "")


def _channel_implies_backend(channel: str) -> str:
    """由通道名推断 resume backend。通道优先于 node.backend(网关图任务尤其关键)。

    返回空串表示无法仅从通道判定(如 runninghub 歧义)。
    """
    ch = (channel or "").strip().lower()
    if not ch:
        return ""
    # 外部插件注入通道: gateway_slotN_<vendor>
    if ch.startswith("gateway_slot") or ch.startswith("gateway_"):
        if "seedance" in ch:
            return "seedance"
        if "happyhorse" in ch:
            return "happyhorse"
        if "seedream" in ch:
            return ""  # 同步端,不可 resume
        if any(
            key in ch
            for key in (
                "gpt_image",
                "banana",
                "flash_image",
                "gemini",
                "nanobanana",
                "mj_",
                "flux",
            )
        ):
            return "gateway-image"
        return ""
    if ch in ("ark",) or ch.startswith("seedance"):
        return "seedance"
    if ch in ("dashscope",) or "happyhorse" in ch:
        return "happyhorse"
    if ch == "rh_app" or ch.startswith("rh_app"):
        return "rh_app"
    if ch in ("comfyui", "comfyui-local"):
        return "comfyui"
    if ch == "gemini":
        return "gemini-image"
    # runninghub: 同名歧义(Seedance 视频 vs Comfy 工作流),禁止只靠通道名
    # 必须由显式 backend 或模型名(seedance*) 在 _infer_backend 中判定
    return ""


def _infer_backend(*, backend: str, model: str, channel: str) -> str:
    """推断 resume 用 backend。

    优先级:
    1. 通道名能唯一判定时,**通道优先**(修正 gateway 图任务落在 gemini-image/gpt-image-2 节点上)
    2. 显式 backend(gpt-image-2 不再伪装成 gemini)
    3. 模型名 / model_registry.node.backend
    """
    ch = (channel or "").strip().lower()
    m = (model or "").strip().lower()
    b = (backend or "").strip().lower()

    ch_backend = _channel_implies_backend(ch)
    if ch_backend:
        return ch_backend

    if b:
        # 原生 gpt-image-2 多为同步 OpenAI 兼容;无 vendor_task_id 语义,禁止走 Gemini resume
        if b == "gpt-image-2":
            return "gpt-image-2"
        return b

    if "seedance" in m:
        return "seedance"
    if "happyhorse" in m:
        return "happyhorse"
    # runninghub 无 model/backend 时 fail-closed 空串,避免误走 comfy/seedance
    if ch == "runninghub":
        if "seedance" in m:
            return "seedance"
        # 无 seedance 关键词时:仅当模型 registry 指 comfy 才在下方解析
        pass
    if "gemini" in m or "banana" in m:
        return "gemini-image"

    try:
        from ..routing.registry import model_registry

        mo = model_registry.get(model) if model else None
        if mo is not None:
            nb = _node_backend(mo)
            if nb == "gpt-image-2":
                return "gpt-image-2"
            if nb:
                return nb
    except Exception:  # noqa: BLE001
        pass
    return ""


def _kind_for_model(model: str, backend: str) -> str:
    try:
        from ..routing.registry import model_registry

        mo = model_registry.get(model) if model else None
        if mo is not None and hasattr(mo, "modality"):
            return str(mo.modality.value)
    except Exception:  # noqa: BLE001
        pass
    m = (model or "").lower()
    if backend in ("seedance", "happyhorse") or "seedance" in m or "happyhorse" in m:
        return "video"
    if backend in ("mimo", "minimax", "fishaudio") or "tts" in m or "speech" in m:
        return "speech"
    return "image"


async def _emit(cb: Optional[ProgressCallback], stage: str, percent: float, message: str) -> None:
    if cb is None:
        return
    try:
        ev = ProgressEvent(stage=stage, percent=percent, message=message)
        res = cb(ev)
        if asyncio.iscoroutine(res):
            await res
    except Exception:  # noqa: BLE001
        pass


async def _call_async(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """调用可能为 async 的 duck-type 方法(resume 对注入通道用)。"""
    if not callable(fn):
        raise TypeError(f"not callable: {fn!r}")
    res = fn(*args, **kwargs)
    if asyncio.iscoroutine(res):
        return await res
    return res


async def _finalize_record(
    record_id: Optional[int],
    *,
    status: str,
    error: str = "",
    elapsed_ms: int = 0,
) -> None:
    """更新统计行终态;失败/取消时有条件退回 RHBind 预扣。

    安全约束:
    - 仅 ``status=running`` 可写终态(已 ok/failed/cancelled 不覆盖、不退款)
    - 先 CAS 抢占终态(防并发双退),**钱包成功后再** ``refunded=True``
      (避免 refund_points 失败却已标退款导致丢积分)
    - ``entry_point=http`` 为 ExternalPrepaid,宿主管钱包,引擎不退 RHBind
    - 其它入口(command/agent 等)原 dispatch 走 PointsBillingPolicy 预扣,须退 RHBind
    """
    if not record_id:
        return
    try:
        from sqlmodel import col, select
        from sqlalchemy import update

        from gsuid_core.utils.database.base_models import async_maker

        from ...utils.database.models import RHComfyuiTaskRecord
        from ...core.billing.points_api import refund_points

        rid = int(record_id)
        need_refund = status in ("failed", "cancelled")
        err = (error or "")[:2000]
        terminal = status[:16]

        cost = 0
        uid = ""
        bid = "default"
        want_refund = False
        wallet_is_rhbind = True

        async with async_maker() as session:
            stmt = select(RHComfyuiTaskRecord).where(col(RHComfyuiTaskRecord.id) == rid)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                logger.warning(f"[resume_poll] finalize 无记录 record_id={rid}")
                return
            cur = (row.status or "").strip().lower()
            if cur != "running":
                logger.info(f"[resume_poll] skip finalize record_id={rid} already status={cur}")
                return

            cost = int(row.point_cost or 0)
            uid = (row.user_id or "").strip()
            bid = (row.bot_id or "").strip() or "default"
            entry = (row.entry_point or "").strip().lower()
            # http = ExternalPrepaid: 宿主钱包,引擎只记 status
            wallet_is_rhbind = entry != "http"
            want_refund = (
                need_refund
                and wallet_is_rhbind
                and not bool(row.refunded)
                and cost > 0
                and bool(uid)
            )

            # 仅抢占终态,不提前标 refunded(钱包成功后再标)
            values: dict[str, Any] = {
                "status": terminal,
                "elapsed_ms": elapsed_ms,
                "error_message": err,
            }
            where = [
                col(RHComfyuiTaskRecord.id) == rid,
                col(RHComfyuiTaskRecord.status) == "running",
            ]
            result = await session.execute(
                update(RHComfyuiTaskRecord).where(*where).values(**values)
            )
            await session.commit()
            claimed = _update_claimed_rows(result)

        if want_refund and claimed:
            try:
                await refund_points(
                    uid,
                    bid,
                    cost,
                    reason=f"resume_{status}:record={rid}",
                )
            except Exception as refund_exc:  # noqa: BLE001
                # 已终态但未退到账:保持 refunded=False 便于对账/人工补退
                logger.error(
                    f"[resume_poll] 终态已写但退款失败 record_id={rid} "
                    f"user={uid} bot={bid} cost={cost}: {refund_exc}"
                )
                return
            async with async_maker() as session:
                mark = await session.execute(
                    update(RHComfyuiTaskRecord)
                    .where(
                        col(RHComfyuiTaskRecord.id) == rid,
                        col(RHComfyuiTaskRecord.refunded) == False,  # noqa: E712
                    )
                    .values(refunded=True)
                )
                await session.commit()
                if not _update_claimed_rows(mark):
                    logger.info(
                        f"[resume_poll] refunded 标记未写入(可能已标) record_id={rid}"
                    )
            logger.info(
                f"[resume_poll] 已退积分 record_id={rid} user={uid} bot={bid} "
                f"+{cost} status={status}"
            )
        elif want_refund and not claimed:
            logger.info(
                f"[resume_poll] finalize 未抢到 record_id={rid}(并发/已终态),跳过退款"
            )
        elif need_refund and not wallet_is_rhbind:
            logger.info(
                f"[resume_poll] entry_point=http 不退 RHBind(宿主钱包) record_id={rid}"
            )
        elif need_refund and wallet_is_rhbind and cost > 0 and not uid:
            logger.warning(f"[resume_poll] 无法退积分:record_id={rid} 无 user_id")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[resume_poll] 更新/退款 record_id={record_id} 失败: {exc}")


# ── 各 backend 实现 ────────────────────────────────────────────────────


def _seedance_channel_map(model: str) -> dict[str, Any]:
    from ..channels.registry import channel_registry
    from ...utils.backends.seedance.channel import builtin_seedance_channels

    ch_map: dict[str, Any] = dict(builtin_seedance_channels())
    try:
        for b in channel_registry.bindings_for(model):
            ch_map[b.channel.name] = b.channel
    except Exception:  # noqa: BLE001
        pass
    return ch_map


def _resolve_seedance_channel(channel: str, model: str) -> tuple[Any, str]:
    """解析 Seedance 通道。显式 channel 缺失时硬失败;空 channel 仅允许默认 ark。"""
    ch_map = _seedance_channel_map(model)
    requested = (channel or "").strip()
    if requested:
        ch = ch_map.get(requested)
        if ch is None:
            raise ResumeNotSupportedError(
                f"Seedance 通道不存在: {requested!r}(可用: {sorted(ch_map.keys())})"
            )
        return ch, requested
    # 兼容遗留行:未记 vendor_channel 时默认 ark
    ch = ch_map.get("ark")
    if ch is None:
        raise ResumeNotSupportedError(
            f"Seedance 未指定 channel 且无 ark 默认通道(可用: {sorted(ch_map.keys())})"
        )
    return ch, "ark"


def _provider_from_channel(ch: Any, ch_name: str) -> Any:
    """优先 ProviderChannel.get_provider_for_resume;拒绝无协议对象。"""
    if isinstance(ch, ProviderChannel):
        provider = ch.get_provider_for_resume()
        if provider is not None:
            return provider
    raise ResumeNotSupportedError(f"通道 {ch_name} 不支持 resume(无 provider)")


def _resume_client_from_channel(ch: Any, ch_name: str) -> Any:
    """优先 ProviderChannel.get_resume_client / 协议方法。"""
    if isinstance(ch, ProviderChannel):
        client = ch.get_resume_client()
        if client is not None:
            return client
    if isinstance(ch, _LegacyResumeClientHost):
        client = ch.get_resume_client()
        if client is not None:
            return client
    # 单测/遗留:仅有 _client 属性
    try:
        client = object.__getattribute__(ch, "_client")
    except AttributeError:
        client = None
    if client is not None:
        return client
    raise ResumeNotSupportedError(
        f"通道 {ch_name} 无可 resume 的 poll client(非异步网关图?)"
    )


def _provider_has_api_key(provider: object | None) -> bool:
    if provider is None:
        return False
    if not isinstance(provider, _HasApiKey):
        return False
    key = provider.api_key
    return bool(key)


async def _resume_seedance(
    *,
    vendor_task_id: str,
    channel: str,
    model: str,
    on_progress: Optional[ProgressCallback],
) -> GenerationResult:
    from ...utils.backends.seedance.channel import _download
    from ...utils.backends.seedance.provider import NormalizedStatus

    ch, ch_name = _resolve_seedance_channel(channel, model)
    provider = _provider_from_channel(ch, ch_name)
    if not _provider_has_api_key(provider):
        raise ResumeNotSupportedError(f"Seedance 通道 {ch_name} 凭证不可用")

    await _emit(on_progress, "resuming", 10, f"恢复 Seedance 轮询({ch_name})")

    final = await provider.poll_until_done(vendor_task_id, on_progress=None)
    if final.status == NormalizedStatus.FAILED:
        err = final.error if hasattr(final, "error") else None
        raise ResumeFailedError(f"Seedance 任务失败: {err or final.raw}")
    if final.status == NormalizedStatus.CANCELLED:
        raise ResumeCancelledError("Seedance 任务已取消")
    if not final.video_url:
        raise ResumeFailedError(f"Seedance 成功但无 video_url: {final.raw}")

    await _emit(on_progress, "downloading", 90, "下载视频")
    video = await _download(final.video_url)
    outputs: dict[str, bytes] = {}
    if final.last_frame_url:
        try:
            outputs["last_frame"] = await _download(final.last_frame_url, timeout=120)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[resume_poll] Seedance 尾帧下载失败: {exc}")

    return GenerationResult(
        kind="video",
        model=model,
        backend="seedance",
        data=video,
        outputs=outputs,
        mime_type="video/mp4",
        usage=dict(final.usage or {}, task_id=final.id, provider=ch_name),
        raw=final.raw,
        metadata={"task_id": final.id, "channel": ch_name, "resumed": True},
    )


def _resolve_happyhorse_channel(channel: str, model: str) -> tuple[Any, str]:
    """按 channel 名从 registry 取 HappyHorse 通道;缺省 builtin dashscope。"""
    from ..channels.registry import channel_registry
    from ...utils.backends.happyhorse.channel import HappyHorseChannel

    requested = (channel or "").strip()
    ch_map: dict[str, Any] = {"dashscope": HappyHorseChannel()}
    try:
        for b in channel_registry.bindings_for(model):
            ch_map[b.channel.name] = b.channel
    except Exception:  # noqa: BLE001
        pass
    if requested:
        ch = ch_map.get(requested)
        if ch is None:
            raise ResumeNotSupportedError(
                f"HappyHorse 通道不存在: {requested!r}(可用: {sorted(ch_map.keys())})"
            )
        return ch, requested
    return ch_map["dashscope"], "dashscope"


async def _resume_happyhorse(
    *,
    vendor_task_id: str,
    channel: str,
    model: str,
    on_progress: Optional[ProgressCallback],
) -> GenerationResult:
    from ...utils.backends.seedance.channel import _download
    from ...utils.backends.seedance.provider import NormalizedStatus
    from ...utils.backends.happyhorse.channel import (
        _dry_run_enabled,
        service_config_credentials,
    )
    from ...utils.backends.happyhorse.provider import HappyHorseProvider

    ch, ch_name = _resolve_happyhorse_channel(channel, model)
    try:
        provider: Any = _provider_from_channel(ch, ch_name)
    except ResumeNotSupportedError:
        provider = None
    if not _provider_has_api_key(provider):
        creds = service_config_credentials()
        if not creds.api_key:
            raise ResumeNotSupportedError("HappyHorse 凭证不可用")
        provider = HappyHorseProvider(
            api_key=creds.api_key,
            base_url=creds.base_url,
            dry_run=_dry_run_enabled(),
        )
    if provider is None:
        raise ResumeNotSupportedError("HappyHorse 凭证不可用")

    await _emit(on_progress, "resuming", 10, f"恢复 HappyHorse 轮询({ch_name})")
    final = await provider.poll_until_done(vendor_task_id, on_progress=None)
    if final.status == NormalizedStatus.FAILED:
        raise ResumeFailedError(f"HappyHorse 任务失败: {final.raw}")
    if final.status == NormalizedStatus.CANCELLED:
        raise ResumeCancelledError("HappyHorse 任务已取消")
    if not final.video_url:
        raise ResumeFailedError(f"HappyHorse 成功但无 video_url: {final.raw}")

    await _emit(on_progress, "downloading", 90, "下载视频")
    video = await _download(final.video_url)
    return GenerationResult(
        kind="video",
        model=model,
        backend="happyhorse",
        data=video,
        mime_type="video/mp4",
        usage=dict(final.usage or {}, task_id=final.id, provider=ch_name),
        raw=final.raw,
        metadata={"task_id": final.id, "channel": ch_name, "resumed": True},
    )


async def _resume_rh_app(
    *,
    vendor_task_id: str,
    model: str,
    on_progress: Optional[ProgressCallback],
) -> GenerationResult:
    """RH AI 应用:可 query 恢复,无 remote cancel。"""
    from ...utils.backends.rh_app.api import rh_app_api

    await _emit(on_progress, "resuming", 10, "恢复 RH App 轮询")
    results = await rh_app_api.wait_for_result(vendor_task_id)
    if not results:
        raise ResumeFailedError("[RHApp] 完成但无 results")

    item = results[0]
    file_url = item.get("url")
    text_content = item.get("text")
    output_type_str = str(item.get("outputType") or "").lower()
    ext_mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
        "mp4": "video/mp4",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "txt": "text/plain",
    }
    mime = ext_mime.get(output_type_str, "image/png")
    kind = "video" if output_type_str == "mp4" else "image"
    if text_content is not None and not file_url:
        return GenerationResult(
            kind="text",
            model=model,
            backend="rh_app",
            data=str(text_content).encode("utf-8"),
            mime_type="text/plain",
            metadata={"task_id": vendor_task_id, "channel": "rh_app", "resumed": True},
        )
    if not file_url:
        raise ResumeFailedError(f"[RHApp] 无文件 URL: {item}")

    await _emit(on_progress, "downloading", 90, "下载产物")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(file_url)
        resp.raise_for_status()
        data = resp.content

    return GenerationResult(
        kind=kind,
        model=model,
        backend="rh_app",
        data=data,
        mime_type=mime,
        raw={"file_url": file_url},
        metadata={"task_id": vendor_task_id, "channel": "rh_app", "resumed": True},
    )


async def _resume_comfyui(
    *,
    vendor_task_id: str,
    channel: str,
    model: str,
    on_progress: Optional[ProgressCallback],
) -> GenerationResult:
    """ComfyUI 工作流(本地或 RH 代理):轮询 history 取 outputs。

    与 rh_app 不同:这里是 /prompt 工作流 prompt_id,可 cancel。
    """
    from ...utils.backends import backend_registry
    from ...utils.backends.comfyui.api import ComfyUIAPI

    await _emit(on_progress, "resuming", 10, f"恢复 ComfyUI 轮询({channel or 'comfyui'})")

    # 复用已注册 adapter 的 api 实例(含 is_runninghub / url)
    adapter = backend_registry.get("comfyui")
    api: Optional[ComfyUIAPI] = None
    if adapter is not None:
        try:
            maybe_api = object.__getattribute__(adapter, "api")
        except AttributeError:
            maybe_api = None
        if isinstance(maybe_api, ComfyUIAPI):
            api = maybe_api
    if api is None:
        api = ComfyUIAPI()

    await api.poll_history_until_complete(vendor_task_id)

    history = await api.get_history(vendor_task_id, log_result=False)
    entry = history[vendor_task_id] if isinstance(history, dict) and vendor_task_id in history else None
    if not entry or not (isinstance(entry, dict) and entry.get("outputs")):
        raise ResumeFailedError(f"ComfyUI history 无产物 prompt_id={vendor_task_id}")

    data = b""
    mime = "image/png"
    kind = "image"
    try:
        images = await api.get_images(vendor_task_id)
        if images:
            for itm in images:
                if isinstance(itm, dict) and itm.get("image_data"):
                    data = itm["image_data"]
                    break
                if isinstance(itm, (bytes, bytearray)):
                    data = bytes(itm)
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[resume_poll] ComfyUI get_images 失败: {exc}")

    if not data:
        try:
            videos = await api.get_videos(vendor_task_id)
            for itm in videos or []:
                if isinstance(itm, dict) and itm.get("video_data"):
                    data = itm["video_data"]
                    mime = "video/mp4"
                    kind = "video"
                    break
                if isinstance(itm, (bytes, bytearray)):
                    data = bytes(itm)
                    mime = "video/mp4"
                    kind = "video"
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[resume_poll] ComfyUI get_videos 失败: {exc}")

    if not data:
        raise ResumeFailedError(f"ComfyUI 无法从 history 提取产物 prompt_id={vendor_task_id}")

    return GenerationResult(
        kind=kind,
        model=model,
        backend="comfyui",
        data=data,
        mime_type=mime,
        metadata={
            "task_id": vendor_task_id,
            "channel": channel or ("runninghub" if api.is_runninghub else "comfyui-local"),
            "resumed": True,
        },
    )


async def _resume_gemini(
    *,
    vendor_task_id: str,
    model: str,
    on_progress: Optional[ProgressCallback],
) -> GenerationResult:
    from ...utils.backends.gemini_image.api import GeminiImageAPI

    api = GeminiImageAPI()
    await _emit(on_progress, "resuming", 10, "恢复 Gemini interaction 轮询")
    if not hasattr(api, "resume_interaction"):
        raise ResumeNotSupportedError("GeminiImageAPI 缺少 resume_interaction")
    payload = await api.resume_interaction(vendor_task_id)
    if not payload:
        raise ResumeFailedError("Gemini 响应未包含图片")

    return GenerationResult(
        kind="image",
        model=model,
        backend="gemini-image",
        data=payload,
        mime_type="image/png",
        metadata={"task_id": vendor_task_id, "channel": "gemini", "resumed": True},
    )


async def _resume_gateway_image(
    *,
    vendor_task_id: str,
    channel: str,
    model: str,
    on_progress: Optional[ProgressCallback],
) -> GenerationResult:
    """聚合网关异步图任务:经 channel_registry 注入的通道 client.poll_until_done。

    不 import 宿主包;优先 ``get_resume_client()`` 公开协议。
    """
    from ..channels.registry import channel_registry
    from ...utils.backends.seedance.channel import _download

    ch_name = (channel or "").strip()
    if not ch_name:
        raise ResumeNotSupportedError("gateway-image resume 必须提供 channel")

    ch: Any | None = None
    try:
        for b in channel_registry.bindings_for(model):
            if b.channel.name == ch_name:
                ch = b.channel
                break
    except Exception as exc:  # noqa: BLE001
        raise ResumeNotSupportedError(f"无法解析通道 {ch_name}: {exc}") from exc
    if ch is None:
        raise ResumeNotSupportedError(f"gateway-image 通道未注册: {ch_name!r}")

    client = _resume_client_from_channel(ch, ch_name)
    if not isinstance(client, _HasPollUntilDone):
        raise ResumeNotSupportedError(
            f"通道 {ch_name} 无可 resume 的 poll client(非异步网关图?)"
        )

    await _emit(on_progress, "resuming", 10, f"恢复网关图任务轮询({ch_name})")
    info = await _call_async(client.poll_until_done, vendor_task_id)
    try:
        phase = str(info.phase or "").lower()
    except AttributeError:
        phase = ""
    if phase in {"cancelled", "canceled"}:
        try:
            err = info.error or phase
        except AttributeError:
            err = phase
        raise ResumeCancelledError(f"网关图任务已取消: {err}")
    if phase in {"failed", "error"}:
        try:
            err = info.error or phase
        except AttributeError:
            err = phase
        raise ResumeFailedError(f"网关图任务失败: {err}")
    try:
        result = info.result
    except AttributeError:
        result = None
    urls: list[str] = []
    if isinstance(result, list):
        urls = [str(u) for u in result if u]
    elif isinstance(result, str) and result:
        urls = [result]
    if not urls:
        try:
            raw = info.raw
        except AttributeError:
            raw = info
        raise ResumeFailedError(f"网关图任务成功但无 resultUrls: {raw}")

    await _emit(on_progress, "downloading", 90, "下载图片")
    data = await _download(urls[0])
    return GenerationResult(
        kind="image",
        model=model,
        backend="gateway-image",
        data=data,
        mime_type="image/png",
        metadata={
            "task_id": vendor_task_id,
            "channel": ch_name,
            "resumed": True,
            "result_urls": urls,
        },
    )


# ── 公开入口 ──────────────────────────────────────────────────────────


async def resume_poll(
    *,
    model: str,
    vendor_task_id: str,
    channel: str = "",
    backend: str = "",
    kind: str = "",
    trace_id: str = "",
    record_id: Optional[int] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> GenerationResult:
    """按上游 task_id 继续轮询直至出结果。

    Args:
        model: 模型 name(与 model_registry / task_name 一致)
        vendor_task_id: 上游任务 id(宿主在 create 成功后持有;resume 只 poll)
        channel: 通道名(ark / dashscope / rh_app / comfyui-local / runninghub / gemini …)
        backend: 后端名(seedance / rh_app / comfyui / …);可空则推断
        kind: 输出模态;可空则推断
        trace_id: 用于 cancel 登记(与 submit 时 trace_id 对齐)
        record_id: 若有则成功/失败时更新 RH 统计行
        on_progress: 进度回调

    Raises:
        ResumeNotSupportedError: 无法 resume(有 record_id 时引擎会先 finalize 再抛)
        ResumeFailedError: 上游失败
        asyncio.CancelledError: 用户取消
    """
    start = time.monotonic()
    ag = None

    try:
        if not vendor_task_id or not str(vendor_task_id).strip():
            raise ResumeNotSupportedError("缺少 vendor_task_id")
        vendor_task_id = str(vendor_task_id).strip()
        model = (model or "").strip()
        channel = (channel or "").strip()
        backend = _infer_backend(backend=backend, model=model, channel=channel)
        if not backend:
            raise ResumeNotSupportedError(
                f"无法推断 backend model={model!r} channel={channel!r}"
            )

        if backend == "seedance":
            # 先拿 provider 再 bind cancel
            result = await _resume_seedance_with_cancel(
                vendor_task_id=vendor_task_id,
                channel=channel,
                model=model,
                on_progress=on_progress,
                trace_id=trace_id,
                record_id=record_id,
            )
        elif backend == "happyhorse":
            result = await _resume_happyhorse_with_cancel(
                vendor_task_id=vendor_task_id,
                channel=channel,
                model=model,
                on_progress=on_progress,
                trace_id=trace_id,
                record_id=record_id,
            )
        elif backend == "rh_app":
            # 仅 resume 继续轮询;禁止 cancel(allow_cancel=False)
            from .active_tasks import get_active_task_registry

            reg = get_active_task_registry()
            ag = await reg.register(
                model_name=model,
                trace_id=trace_id,
                record_id=record_id,
                allow_cancel=False,
            )
            await reg.bind_vendor_task(
                vendor_task_id=vendor_task_id,
                channel_name="rh_app",
                cancel_remote=None,
                ag=ag,
            )
            try:
                result = await _resume_rh_app(
                    vendor_task_id=vendor_task_id,
                    model=model,
                    on_progress=on_progress,
                )
            finally:
                await reg.unregister(ag)
        elif backend == "comfyui":
            result = await _resume_comfyui_with_cancel(
                vendor_task_id=vendor_task_id,
                channel=channel,
                model=model,
                on_progress=on_progress,
                trace_id=trace_id,
                record_id=record_id,
            )
        elif backend == "gemini-image":
            result = await _resume_gemini_with_cancel(
                vendor_task_id=vendor_task_id,
                model=model,
                on_progress=on_progress,
                trace_id=trace_id,
                record_id=record_id,
            )
        elif backend == "gateway-image":
            result = await _resume_gateway_image_with_cancel(
                vendor_task_id=vendor_task_id,
                channel=channel,
                model=model,
                on_progress=on_progress,
                trace_id=trace_id,
                record_id=record_id,
            )
        elif backend == "gpt-image-2":
            raise ResumeNotSupportedError(
                "gpt-image-2 原生同步端不支持 resume-poll;"
                "网关异步图请传 vendor_channel=gateway_slot*_gpt_image_*"
            )
        else:
            raise ResumeNotSupportedError(f"backend={backend!r} 暂不支持 resume-poll")

        elapsed_ms = int((time.monotonic() - start) * 1000)
        result.elapsed_ms = elapsed_ms
        if not result.kind and kind:
            result.kind = kind
        await _finalize_record(record_id, status="ok", elapsed_ms=elapsed_ms)
        await _emit(on_progress, "done", 100, "恢复完成")
        return result
    except asyncio.CancelledError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        await _finalize_record(
            record_id, status="cancelled", error="user_cancel", elapsed_ms=elapsed_ms
        )
        raise
    except ResumeCancelledError as exc:
        # 上游已 cancel:统计 status=cancelled(勿记 failed)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        await _finalize_record(
            record_id,
            status="cancelled",
            error=str(exc)[:2000],
            elapsed_ms=elapsed_ms,
        )
        raise
    except ResumeNotSupportedError as exc:
        # 与 ResumeFailedError 一致:有 record_id 时落终态并条件退款,避免 running 悬挂
        elapsed_ms = int((time.monotonic() - start) * 1000)
        await _finalize_record(
            record_id,
            status="failed",
            error=str(exc)[:2000],
            elapsed_ms=elapsed_ms,
        )
        raise
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        await _finalize_record(
            record_id,
            status="failed",
            error=str(exc)[:2000],
            elapsed_ms=elapsed_ms,
        )
        if isinstance(exc, ResumeFailedError):
            raise
        raise ResumeFailedError(str(exc)) from exc


async def _resume_seedance_with_cancel(**kwargs: Any) -> GenerationResult:
    from .active_tasks import get_active_task_registry

    model = kwargs["model"]
    channel = kwargs.get("channel") or ""
    vendor_task_id = kwargs["vendor_task_id"]
    ch, ch_name = _resolve_seedance_channel(channel, model)
    provider = _provider_from_channel(ch, ch_name)

    reg = get_active_task_registry()
    ag = await reg.register(
        model_name=model,
        trace_id=kwargs.get("trace_id") or "",
        record_id=kwargs.get("record_id"),
    )
    cancel_remote = None
    if isinstance(provider, _HasDelete) and _provider_supports_remote_cancel(provider):
        p = provider

        async def _cancel() -> None:
            await p.delete(vendor_task_id)

        cancel_remote = _cancel
    await reg.bind_vendor_task(
        vendor_task_id=vendor_task_id,
        cancel_remote=cancel_remote,
        channel_name=ch_name,
        ag=ag,
    )
    try:
        return await _resume_seedance(
            vendor_task_id=vendor_task_id,
            channel=ch_name,
            model=model,
            on_progress=kwargs.get("on_progress"),
        )
    finally:
        await reg.unregister(ag)


async def _resume_happyhorse_with_cancel(**kwargs: Any) -> GenerationResult:
    from .active_tasks import get_active_task_registry

    model = kwargs["model"]
    vendor_task_id = kwargs["vendor_task_id"]
    channel = kwargs.get("channel") or ""
    ch, ch_name = _resolve_happyhorse_channel(channel, model)
    try:
        provider = _provider_from_channel(ch, ch_name)
    except ResumeNotSupportedError:
        provider = None
    reg = get_active_task_registry()
    ag = await reg.register(
        model_name=model,
        trace_id=kwargs.get("trace_id") or "",
        record_id=kwargs.get("record_id"),
    )
    cancel_remote = None
    # fail-closed:与 Seedance 一致,缺方法不默认 True
    if isinstance(provider, _HasDelete) and _provider_supports_remote_cancel(provider):
        p = provider

        async def _cancel() -> None:
            await p.delete(vendor_task_id)

        cancel_remote = _cancel
    await reg.bind_vendor_task(
        vendor_task_id=vendor_task_id,
        cancel_remote=cancel_remote,
        channel_name=ch_name,
        ag=ag,
    )
    try:
        return await _resume_happyhorse(
            vendor_task_id=vendor_task_id,
            channel=ch_name,
            model=model,
            on_progress=kwargs.get("on_progress"),
        )
    finally:
        await reg.unregister(ag)


async def _resume_comfyui_with_cancel(**kwargs: Any) -> GenerationResult:
    from .active_tasks import get_active_task_registry
    from ...utils.backends import backend_registry
    from ...utils.backends.comfyui.api import ComfyUIAPI

    model = kwargs["model"]
    vendor_task_id = kwargs["vendor_task_id"]
    channel = kwargs.get("channel") or "comfyui"
    adapter = backend_registry.get("comfyui")
    api: ComfyUIAPI | None = None
    if adapter is not None:
        try:
            maybe_api = object.__getattribute__(adapter, "api")
        except AttributeError:
            maybe_api = None
        if isinstance(maybe_api, ComfyUIAPI):
            api = maybe_api

    reg = get_active_task_registry()
    ag = await reg.register(
        model_name=model,
        trace_id=kwargs.get("trace_id") or "",
        record_id=kwargs.get("record_id"),
    )
    cancel_remote = None
    if api is not None:

        async def _cancel() -> None:
            await api.cancel_task(vendor_task_id)

        cancel_remote = _cancel
    await reg.bind_vendor_task(
        vendor_task_id=vendor_task_id,
        cancel_remote=cancel_remote,
        channel_name=channel,
        ag=ag,
    )
    try:
        return await _resume_comfyui(
            vendor_task_id=vendor_task_id,
            channel=channel,
            model=model,
            on_progress=kwargs.get("on_progress"),
        )
    finally:
        await reg.unregister(ag)


async def _resume_gemini_with_cancel(**kwargs: Any) -> GenerationResult:
    from .active_tasks import get_active_task_registry
    from ...utils.backends.gemini_image.api import GeminiImageAPI

    model = kwargs["model"]
    vendor_task_id = kwargs["vendor_task_id"]
    api = GeminiImageAPI()
    reg = get_active_task_registry()
    ag = await reg.register(
        model_name=model,
        trace_id=kwargs.get("trace_id") or "",
        record_id=kwargs.get("record_id"),
    )

    async def _cancel() -> None:
        await api.cancel_interaction(vendor_task_id)

    await reg.bind_vendor_task(
        vendor_task_id=vendor_task_id,
        cancel_remote=_cancel,
        channel_name="gemini",
        ag=ag,
    )
    try:
        return await _resume_gemini(
            vendor_task_id=vendor_task_id,
            model=model,
            on_progress=kwargs.get("on_progress"),
        )
    finally:
        await reg.unregister(ag)


async def _resume_gateway_image_with_cancel(**kwargs: Any) -> GenerationResult:
    from .active_tasks import get_active_task_registry
    from ..channels.registry import channel_registry

    model = kwargs["model"]
    vendor_task_id = kwargs["vendor_task_id"]
    channel = (kwargs.get("channel") or "").strip()
    if not channel:
        raise ResumeNotSupportedError("gateway-image resume 必须提供 channel")

    ch: Any | None = None
    try:
        for b in channel_registry.bindings_for(model):
            if b.channel.name == channel:
                ch = b.channel
                break
    except Exception:  # noqa: BLE001
        ch = None

    reg = get_active_task_registry()
    ag = await reg.register(
        model_name=model,
        trace_id=kwargs.get("trace_id") or "",
        record_id=kwargs.get("record_id"),
    )
    cancel_remote = None
    client: Any | None = None
    if ch is not None:
        try:
            client = _resume_client_from_channel(ch, channel)
        except ResumeNotSupportedError:
            client = None
    if isinstance(client, _HasDeleteTask):
        resume_client = client

        async def _cancel() -> None:
            await resume_client.delete_task(vendor_task_id)

        cancel_remote = _cancel
    await reg.bind_vendor_task(
        vendor_task_id=vendor_task_id,
        cancel_remote=cancel_remote,
        channel_name=channel,
        ag=ag,
    )
    try:
        return await _resume_gateway_image(
            vendor_task_id=vendor_task_id,
            channel=channel,
            model=model,
            on_progress=kwargs.get("on_progress"),
        )
    finally:
        await reg.unregister(ag)


def can_resume(*, backend: str = "", model: str = "", channel: str = "", vendor_task_id: str = "") -> bool:
    """粗判是否具备 resume 条件(有 task_id + 已知异步后端)。"""
    if not vendor_task_id:
        return False
    b = _infer_backend(backend=backend, model=model, channel=channel)
    if b == "gpt-image-2":
        return False
    return b in {
        "seedance",
        "happyhorse",
        "rh_app",
        "comfyui",
        "gemini-image",
        "gateway-image",
    }


__all__ = [
    "ResumeNotSupportedError",
    "ResumeFailedError",
    "ResumeCancelledError",
    "resume_poll",
    "can_resume",
]
