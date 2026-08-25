"""Seedance 参考视频预处理 — 时长钳位 + r2v 最低像素放大

上游约束:
- 参考视频时长 2~15s(2.0) / 2~30s(2.5);过短循环,过长裁切
- Seedance 2.5 r2v:视频像素数须 ≥ 407696,否则 InvalidParameter

策略:
- 一次 ffprobe 同时读 duration + 宽高,避免重复落盘
- 时长钳位与像素放大合并进**同一趟** ffmpeg,禁止 trim 后再 scale
- 全局 Semaphore 限制并发 ffmpeg(默认 2),单进程 ``-threads`` 再限核
- 已合法则原样返回,不重编码

依赖系统 PATH 中的 ``ffmpeg`` / ``ffprobe``;不可用时 warning 并放行原字节。
"""

from __future__ import annotations

import os
import math
import shutil
import asyncio
import tempfile
from typing import Any, Optional
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from gsuid_core.logger import logger

# Seedance 官方硬限(2.0 默认;2.5 由调用方改 max)
SEEDANCE_REF_VIDEO_MIN_S = 2.0
SEEDANCE_REF_VIDEO_MAX_S = 15.0
SEEDANCE_REF_VIDEO_LOOP_TARGET_S = 2.5
SEEDANCE_REF_VIDEO_TRIM_TARGET_S = 14.5

# doubao-seedance-2-5 r2v: video pixel count >= 407696
SEEDANCE_REF_VIDEO_MIN_PIXELS = 407696

_FFMPEG_TIMEOUT_S = 120.0
_FFMPEG_CONCURRENCY = max(1, int(os.environ.get("SEEDANCE_FFMPEG_CONCURRENCY", "2")))
_FFMPEG_THREADS = max(1, int(os.environ.get("SEEDANCE_FFMPEG_THREADS", "2")))

_ffmpeg_sem: Optional[asyncio.Semaphore] = None


@dataclass(frozen=True)
class RefVideoClampSpec:
    """提交前参考视频钳位;min_pixels=0 表示不放大(H3 无 Seedance 像素硬限)。"""

    min_s: float = SEEDANCE_REF_VIDEO_MIN_S
    max_s: float = SEEDANCE_REF_VIDEO_MAX_S
    loop_target_s: float = SEEDANCE_REF_VIDEO_LOOP_TARGET_S
    trim_target_s: float = SEEDANCE_REF_VIDEO_TRIM_TARGET_S
    min_pixels: int = 0


_REF_VIDEO_CLAMP: ContextVar[Optional[RefVideoClampSpec]] = ContextVar(
    "rh_ref_video_clamp",
    default=None,
)


def is_seedance25_vendor_model(model: Optional[str]) -> bool:
    """识别 2.5 vendor id(ark 日期编码 / 网关点分 / host 名)。"""
    if not model:
        return False
    m = model.strip().lower()
    return "seedance-2-5" in m or "seedance-2.5" in m or m.endswith("seedance2.5")


def seedance_ref_video_clamp_for_vendor(model: Optional[str]) -> RefVideoClampSpec:
    """Seedance 2.0=15s;2.5=30s。像素下限两代共用。"""
    if is_seedance25_vendor_model(model):
        return RefVideoClampSpec(
            max_s=30.0,
            trim_target_s=29.5,
            min_pixels=SEEDANCE_REF_VIDEO_MIN_PIXELS,
        )
    return RefVideoClampSpec(
        max_s=15.0,
        trim_target_s=14.5,
        min_pixels=SEEDANCE_REF_VIDEO_MIN_PIXELS,
    )


def current_ref_video_clamp() -> Optional[RefVideoClampSpec]:
    return _REF_VIDEO_CLAMP.get()


@contextmanager
def use_ref_video_clamp(spec: Optional[RefVideoClampSpec]):
    """render_create / materialize 期间绑定钳位;并发任务各有独立 ContextVar。"""
    token = _REF_VIDEO_CLAMP.set(spec)
    try:
        yield spec
    finally:
        _REF_VIDEO_CLAMP.reset(token)


class VideoProcessError(RuntimeError):
    """视频预处理失败(探测/编码);调用方可选择放行原片或改报错。"""


def ffmpeg_semaphore() -> asyncio.Semaphore:
    """进程内共享的 ffmpeg 并发闸(视频缩放 + 音频裁切共用)。"""
    global _ffmpeg_sem
    if _ffmpeg_sem is None:
        _ffmpeg_sem = asyncio.Semaphore(_FFMPEG_CONCURRENCY)
    return _ffmpeg_sem


def ffmpeg_thread_count() -> int:
    return _FFMPEG_THREADS


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def seedance_scale_for_min_pixels(
    width: int,
    height: int,
    min_pixels: int = SEEDANCE_REF_VIDEO_MIN_PIXELS,
) -> tuple[int, int]:
    """等比放大到 w*h ≥ min_pixels,宽高均为偶数(yuv420p)。已满足则原样返回。"""
    w = int(width)
    h = int(height)
    if w < 2 or h < 2 or min_pixels <= 0:
        return max(w, 0), max(h, 0)
    if w * h >= min_pixels:
        return w, h
    scale = math.sqrt(min_pixels / (w * h))
    nw = max(2, int(math.ceil(w * scale / 2.0) * 2))
    nh = max(2, int(math.ceil(h * scale / 2.0) * 2))
    # 取整后可能仍差几个像素,按较短边步进 +2
    while nw * nh < min_pixels:
        if nw / w <= nh / h:
            nw += 2
        else:
            nh += 2
    return nw, nh


async def _run_exec(args: list[str], *, timeout: float) -> tuple[int, bytes, bytes]:
    """跑一条 ffmpeg/ffprobe:先拿并发闸再起进程,避免排队任务先占满进程表。"""
    async with ffmpeg_semaphore():
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise
    return proc.returncode or 0, stdout, stderr


async def probe_video_meta(data: bytes) -> tuple[float, int, int]:
    """ffprobe 读 (duration, width, height);失败返回 (0, 0, 0)。"""
    ffprobe = _which("ffprobe")
    if not ffprobe or not data:
        return 0.0, 0, 0
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        return await probe_video_meta_path(tmp_path)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


async def probe_video_meta_path(path: Path) -> tuple[float, int, int]:
    """从已落盘文件探测 (duration, width, height)。"""
    ffprobe = _which("ffprobe")
    if not ffprobe:
        return 0.0, 0, 0
    try:
        code, stdout, _stderr = await _run_exec(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=0",
                str(path),
            ],
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        return 0.0, 0, 0
    if code != 0:
        return 0.0, 0, 0
    duration = 0.0
    width = 0
    height = 0
    for raw_line in stdout.decode(errors="replace").splitlines():
        line = raw_line.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().lower()
        val = val.strip()
        if key == "duration":
            try:
                duration = float(val)
            except ValueError:
                pass
        elif key == "width":
            try:
                width = int(float(val))
            except ValueError:
                pass
        elif key == "height":
            try:
                height = int(float(val))
            except ValueError:
                pass
    return duration, width, height


async def probe_video_duration(data: bytes) -> float:
    """ffprobe 读时长(秒);失败返回 0。"""
    duration, _w, _h = await probe_video_meta(data)
    return duration


async def _run_ffmpeg(args: list[str]) -> None:
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise VideoProcessError("未在 PATH 中找到 ffmpeg")
    try:
        code, _stdout, stderr = await _run_exec(
            [ffmpeg, *args],
            timeout=_FFMPEG_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise VideoProcessError(f"ffmpeg 超时({_FFMPEG_TIMEOUT_S}s)") from exc
    if code != 0:
        msg = stderr.decode(errors="replace")[-800:]
        raise VideoProcessError(f"ffmpeg 失败: {msg}")


async def _encode_ref_video(
    src: Path,
    dst: Path,
    *,
    target_s: Optional[float],
    loop: bool,
    scale_wh: Optional[tuple[int, int]],
) -> None:
    """一趟重编码:可选循环/裁时长 + 可选等比放大。"""
    args: list[str] = [
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        str(_FFMPEG_THREADS),
    ]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", str(src)]
    if target_s is not None:
        args += ["-t", f"{target_s:.3f}"]
    args += ["-map", "0:v:0", "-map", "0:a:0?"]
    if scale_wh is not None:
        sw, sh = scale_wh
        args += ["-vf", f"scale={sw}:{sh}:flags=lanczos,setsar=1"]
    args += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dst),
    ]
    await _run_ffmpeg(args)


async def prepare_seedance_ref_video(
    data: bytes,
    *,
    min_s: float = SEEDANCE_REF_VIDEO_MIN_S,
    max_s: float = SEEDANCE_REF_VIDEO_MAX_S,
    loop_target_s: float = SEEDANCE_REF_VIDEO_LOOP_TARGET_S,
    trim_target_s: float = SEEDANCE_REF_VIDEO_TRIM_TARGET_S,
    min_pixels: int = SEEDANCE_REF_VIDEO_MIN_PIXELS,
) -> tuple[bytes, float, Optional[str]]:
    """把参考视频钳到 Seedance 合法时长,并在像素不足时等比放大。

    Returns:
        ``(bytes, duration_after, action)``
        action: None=未改; ``"loop"`` / ``"trim"`` / ``"scale"`` /
        ``"loop+scale"`` / ``"trim+scale"``;探测失败时 duration=0 且 action=None
    """
    if not data:
        return data, 0.0, None
    if not _which("ffmpeg") or not _which("ffprobe"):
        logger.warning("[video_process] ffmpeg/ffprobe 不可用,跳过参考视频预处理")
        return data, 0.0, None

    src_path: Optional[Path] = None
    dst_path: Optional[Path] = None
    duration = 0.0
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src:
            src.write(data)
            src_path = Path(src.name)

        duration, width, height = await probe_video_meta_path(src_path)
        if duration <= 0:
            logger.warning("[video_process] 无法探测参考视频时长,跳过预处理 size=%d", len(data))
            return data, 0.0, None

        need_loop = duration < min_s
        need_trim = duration > max_s
        need_scale = False
        scale_wh: Optional[tuple[int, int]] = None
        if min_pixels > 0 and width >= 2 and height >= 2 and width * height < min_pixels:
            scale_wh = seedance_scale_for_min_pixels(width, height, min_pixels)
            need_scale = scale_wh != (width, height)

        if not need_loop and not need_trim and not need_scale:
            return data, duration, None

        action_parts: list[str] = []
        target: Optional[float] = None
        loop = False
        if need_loop:
            action_parts.append("loop")
            target = loop_target_s
            loop = True
        elif need_trim:
            action_parts.append("trim")
            target = trim_target_s
        if need_scale:
            action_parts.append("scale")
        action = "+".join(action_parts)

        fd, dst_name = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        dst_path = Path(dst_name)

        await _encode_ref_video(
            src_path,
            dst_path,
            target_s=target,
            loop=loop,
            scale_wh=scale_wh,
        )
        out = dst_path.read_bytes()
        new_dur, new_w, new_h = await probe_video_meta_path(dst_path)
        if new_dur <= 0:
            new_dur = target if target is not None else duration
        logger.info(
            f"[video_process] 参考视频预处理 action={action} "
            f"{duration:.2f}s {width}x{height} → {new_dur:.2f}s {new_w}x{new_h} "
            f"size={len(data)}→{len(out)}"
        )
        return out, new_dur, action
    except VideoProcessError as exc:
        logger.warning("[video_process] 参考视频预处理失败,放行原片: %s", exc)
        return data, duration if src_path else 0.0, None
    finally:
        for p in (src_path, dst_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


async def clamp_seedance_ref_video(
    data: bytes,
    *,
    min_s: float = SEEDANCE_REF_VIDEO_MIN_S,
    max_s: float = SEEDANCE_REF_VIDEO_MAX_S,
    loop_target_s: float = SEEDANCE_REF_VIDEO_LOOP_TARGET_S,
    trim_target_s: float = SEEDANCE_REF_VIDEO_TRIM_TARGET_S,
    min_pixels: int = SEEDANCE_REF_VIDEO_MIN_PIXELS,
) -> tuple[bytes, float, Optional[str]]:
    """兼容旧名:等同 ``prepare_seedance_ref_video``。"""
    return await prepare_seedance_ref_video(
        data,
        min_s=min_s,
        max_s=max_s,
        loop_target_s=loop_target_s,
        trim_target_s=trim_target_s,
        min_pixels=min_pixels,
    )


async def ensure_media_bytes(ref) -> Optional[bytes]:
    """MediaRef → bytes:优先 data,否则 http(s) 下载;失败返回 None。"""
    if ref.data:
        return ref.data
    url = (ref.url or "").strip()
    if not url:
        return None
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        return None
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("[video_process] 下载参考媒体失败 url=%s err=%s", url[:120], exc)
        return None


async def prepare_seedance_video_ref(
    ref: Any,
    *,
    min_s: float = SEEDANCE_REF_VIDEO_MIN_S,
    max_s: float = SEEDANCE_REF_VIDEO_MAX_S,
    loop_target_s: float = SEEDANCE_REF_VIDEO_LOOP_TARGET_S,
    trim_target_s: float = SEEDANCE_REF_VIDEO_TRIM_TARGET_S,
    min_pixels: int = SEEDANCE_REF_VIDEO_MIN_PIXELS,
) -> Any:
    """参考视频提交前改写:时长钳位 + 可选像素放大,并清掉旧 url。

    ``asset://``、非视频、取不到字节、已合法时原样返回。须在 materialize
    上传/透传之前调用,避免 http URL 把超限原片交给上游。
    """
    from .core.types import MediaRef, MediaKind

    if not isinstance(ref, MediaRef) or ref.kind != MediaKind.VIDEO:
        return ref
    url = (ref.url or "").strip()
    if url.lower().startswith("asset://"):
        return ref
    raw = ref.data if ref.data else await ensure_media_bytes(ref)
    if not raw:
        return ref
    new_data, _dur, action = await prepare_seedance_ref_video(
        raw,
        min_s=min_s,
        max_s=max_s,
        loop_target_s=loop_target_s,
        trim_target_s=trim_target_s,
        min_pixels=min_pixels,
    )
    if action is None and new_data is raw:
        return ref
    return MediaRef(
        kind=MediaKind.VIDEO,
        data=new_data,
        url=None,
        role=ref.role,
        mime_type="video/mp4",
        filename=ref.filename,
    )


async def prepare_ref_video_if_clamping(ref: Any) -> Any:
    """若当前任务绑了 RefVideoClampSpec,按该规格预处理参考视频。"""
    clamp = current_ref_video_clamp()
    if clamp is None:
        return ref
    return await prepare_seedance_video_ref(
        ref,
        min_s=clamp.min_s,
        max_s=clamp.max_s,
        loop_target_s=clamp.loop_target_s,
        trim_target_s=clamp.trim_target_s,
        min_pixels=clamp.min_pixels,
    )


__all__ = [
    "SEEDANCE_REF_VIDEO_MIN_S",
    "SEEDANCE_REF_VIDEO_MAX_S",
    "SEEDANCE_REF_VIDEO_LOOP_TARGET_S",
    "SEEDANCE_REF_VIDEO_TRIM_TARGET_S",
    "SEEDANCE_REF_VIDEO_MIN_PIXELS",
    "RefVideoClampSpec",
    "VideoProcessError",
    "ffmpeg_semaphore",
    "ffmpeg_thread_count",
    "is_seedance25_vendor_model",
    "seedance_ref_video_clamp_for_vendor",
    "current_ref_video_clamp",
    "use_ref_video_clamp",
    "seedance_scale_for_min_pixels",
    "probe_video_meta",
    "probe_video_meta_path",
    "probe_video_duration",
    "prepare_seedance_ref_video",
    "clamp_seedance_ref_video",
    "prepare_seedance_video_ref",
    "prepare_ref_video_if_clamping",
    "ensure_media_bytes",
]
