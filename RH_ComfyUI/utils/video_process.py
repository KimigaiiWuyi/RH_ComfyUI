"""参考视频时长钳位 — Seedance 2.x 输入视频要求 [2, 15] 秒

上游 Seedance API 对参考视频硬限制 2~15s。调用方可能连入更短/更长的片段,
本模块在提交前:

- ``duration < 2``  → 循环复制拉长到 **2.5s**(留一点余量,避免边界被拒)
- ``duration > 15`` → 裁切到 **14.5s**
- 区间内原样返回

依赖系统 PATH 中的 ``ffmpeg`` / ``ffprobe``;不可用时记录 warning 并放行原字节
(不阻断任务,由上游自己拒或成功)。
"""

from __future__ import annotations

import os
import shutil
import asyncio
import tempfile
from typing import Optional
from pathlib import Path

from gsuid_core.logger import logger

# Seedance 官方硬限
SEEDANCE_REF_VIDEO_MIN_S = 2.0
SEEDANCE_REF_VIDEO_MAX_S = 15.0
# 鲁棒目标(离边界留 0.5s 缓冲)
SEEDANCE_REF_VIDEO_LOOP_TARGET_S = 2.5
SEEDANCE_REF_VIDEO_TRIM_TARGET_S = 14.5

_FFMPEG_TIMEOUT_S = 120.0


class VideoProcessError(RuntimeError):
    """视频预处理失败(探测/编码);调用方可选择放行原片或改报错。"""


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


async def probe_video_duration(data: bytes) -> float:
    """ffprobe 读时长(秒);失败返回 0。"""
    ffprobe = _which("ffprobe")
    if not ffprobe or not data:
        return 0.0
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(tmp_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return 0.0
        if proc.returncode != 0:
            return 0.0
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


async def _run_ffmpeg(args: list[str]) -> None:
    ffmpeg = _which("ffmpeg")
    if not ffmpeg:
        raise VideoProcessError("未在 PATH 中找到 ffmpeg")
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_FFMPEG_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise VideoProcessError(f"ffmpeg 超时({_FFMPEG_TIMEOUT_S}s)") from exc
    if proc.returncode != 0:
        msg = stderr.decode(errors="replace")[-800:]
        raise VideoProcessError(f"ffmpeg 失败: {msg}")


async def _encode_to_duration(src: Path, dst: Path, *, target_s: float, loop: bool) -> None:
    """重编码到固定时长。

    loop=True 时用 ``-stream_loop -1`` 循环输入再 ``-t target`` 截断;
    loop=False 时从开头裁到 target。
    音频流可选(``0:a:0?``);无音轨时只出视频。
    """
    # 统一重编码,避免 -c copy 在非关键帧裁切失败
    args: list[str] = ["-y", "-hide_banner", "-loglevel", "error"]
    if loop:
        args += ["-stream_loop", "-1"]
    args += [
        "-i",
        str(src),
        "-t",
        f"{target_s:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
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


async def clamp_seedance_ref_video(
    data: bytes,
    *,
    min_s: float = SEEDANCE_REF_VIDEO_MIN_S,
    max_s: float = SEEDANCE_REF_VIDEO_MAX_S,
    loop_target_s: float = SEEDANCE_REF_VIDEO_LOOP_TARGET_S,
    trim_target_s: float = SEEDANCE_REF_VIDEO_TRIM_TARGET_S,
) -> tuple[bytes, float, Optional[str]]:
    """把参考视频钳到 Seedance 合法时长。

    Returns:
        ``(bytes, duration_after, action)``
        - action: None=未改; ``"loop"`` / ``"trim"``; 探测失败时 duration=0 且 action=None
    """
    if not data:
        return data, 0.0, None
    if not _which("ffmpeg") or not _which("ffprobe"):
        logger.warning("[video_process] ffmpeg/ffprobe 不可用,跳过参考视频时长钳位")
        return data, 0.0, None

    duration = await probe_video_duration(data)
    if duration <= 0:
        logger.warning("[video_process] 无法探测参考视频时长,跳过钳位 size=%d", len(data))
        return data, 0.0, None

    if min_s <= duration <= max_s:
        return data, duration, None

    action: str
    target: float
    loop: bool
    if duration < min_s:
        action = "loop"
        target = loop_target_s
        loop = True
    else:
        action = "trim"
        target = trim_target_s
        loop = False

    src_path: Optional[Path] = None
    dst_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src:
            src.write(data)
            src_path = Path(src.name)
        fd, dst_name = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        dst_path = Path(dst_name)

        await _encode_to_duration(src_path, dst_path, target_s=target, loop=loop)
        out = dst_path.read_bytes()
        new_dur = await probe_video_duration(out) or target
        logger.info(
            f"[video_process] 参考视频时长钳位 action={action} "
            f"{duration:.2f}s → {new_dur:.2f}s (target={target:.2f}) "
            f"size={len(data)}→{len(out)}"
        )
        return out, new_dur, action
    except VideoProcessError as exc:
        logger.warning("[video_process] 参考视频时长钳位失败,放行原片: %s", exc)
        return data, duration, None
    finally:
        for p in (src_path, dst_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


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
        logger.warning("[video_process] 下载参考视频失败 url=%s err=%s", url[:120], exc)
        return None


__all__ = [
    "SEEDANCE_REF_VIDEO_MIN_S",
    "SEEDANCE_REF_VIDEO_MAX_S",
    "SEEDANCE_REF_VIDEO_LOOP_TARGET_S",
    "SEEDANCE_REF_VIDEO_TRIM_TARGET_S",
    "VideoProcessError",
    "probe_video_duration",
    "clamp_seedance_ref_video",
    "ensure_media_bytes",
]
