"""Seedance 参考音频预处理 — r2v 时长裁切

上游硬限:Seedance 2.0 单段/合计 ≤15.2s;2.5 放宽到单段/合计 ≤30s。
过长时从开头裁到上限(留 0.2s 余量)。Seedance 只接受 wav / mp3:
保留这两种容器,其余格式(m4a/aac/ogg/...)统一转 mp3。
探测/编码失败放行原片。

与 ``video_process`` 共用 ffmpeg 并发闸,避免音视频同时重编码打满 CPU。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional
from pathlib import Path

from gsuid_core.logger import logger

from .video_process import VideoProcessError, _run_exec, ffmpeg_thread_count

SEEDANCE_REF_AUDIO_MAX_S = 15.2
SEEDANCE_REF_AUDIO_TRIM_TARGET_S = 15.0

_FFMPEG_TIMEOUT_S = 60.0

_AUDIO_MIME_BY_SUFFIX = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}


def _audio_mime(suffix: str) -> str:
    return _AUDIO_MIME_BY_SUFFIX.get(suffix, "audio/mpeg")


def _output_suffix(src_suffix: str) -> str:
    """Seedance 只收 wav/mp3:保留这两种容器,其余统一转 mp3。"""
    return src_suffix if src_suffix in (".mp3", ".wav") else ".mp3"


def _encode_args(out_suffix: str) -> list[str]:
    if out_suffix == ".wav":
        return ["-c:a", "pcm_s16le"]
    return ["-c:a", "libmp3lame", "-b:a", "128k"]


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _guess_suffix(data: bytes, mime: str = "") -> str:
    """判断音频容器;文件头优先,调用方 MIME 只作提示(扩展名可能和内容不一致)。"""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return ".wav"
    if data[:3] == b"ID3":
        return ".mp3"
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        # ADTS AAC 的 layer bits 为 00;MP3 层号非 0
        return ".aac" if (data[1] >> 1) & 0x03 == 0 else ".mp3"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".m4a"
    if data[:4] == b"OggS":
        return ".ogg"

    low = (mime or "").lower()
    if "mpeg" in low or low.endswith("/mp3"):
        return ".mp3"
    if "wav" in low:
        return ".wav"
    if "ogg" in low or "opus" in low:
        return ".ogg"
    if "aac" in low:
        return ".aac"
    return ".m4a"


async def probe_audio_duration(data: bytes, *, suffix: str = ".m4a") -> float:
    """ffprobe 读音频时长(秒);失败返回 0。"""
    ffprobe = _which("ffprobe")
    if not ffprobe or not data:
        return 0.0
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        code, stdout, _stderr = await _run_exec(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(tmp_path),
            ],
            timeout=30.0,
        )
        if code != 0:
            return 0.0
        try:
            return float(stdout.decode().strip())
        except ValueError:
            return 0.0
    except Exception:  # noqa: BLE001
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
    try:
        code, _stdout, stderr = await _run_exec(
            [ffmpeg, *args],
            timeout=_FFMPEG_TIMEOUT_S,
        )
    except Exception as exc:
        raise VideoProcessError(f"ffmpeg 超时或失败: {exc}") from exc
    if code != 0:
        msg = stderr.decode(errors="replace")[-800:]
        raise VideoProcessError(f"ffmpeg 失败: {msg}")


async def clamp_seedance_ref_audio(
    data: bytes,
    *,
    max_s: float = SEEDANCE_REF_AUDIO_MAX_S,
    trim_target_s: float = SEEDANCE_REF_AUDIO_TRIM_TARGET_S,
    mime_type: str = "",
) -> tuple[bytes, float, Optional[str], str]:
    """把参考音频裁到上限,并规整为 Seedance 支持的容器。

    Seedance 只接受 wav / mp3:源为 wav/mp3 时保留原容器;
    其它格式(m4a/aac/ogg/...)统一转 mp3。超长才裁,否则只转容器。

    Returns:
        ``(bytes, duration_after, action, mime)``;
        action 为 None / ``"trim"`` / ``"transcode"``。
    """
    suffix = _guess_suffix(data, mime_type)
    input_mime = _audio_mime(suffix)
    if not data:
        return data, 0.0, None, input_mime
    if not _which("ffmpeg") or not _which("ffprobe"):
        logger.warning("[audio_process] ffmpeg/ffprobe 不可用,跳过参考音频处理")
        return data, 0.0, None, input_mime

    duration = await probe_audio_duration(data, suffix=suffix)
    if duration <= 0:
        logger.warning("[audio_process] 无法探测参考音频时长,跳过处理 size=%d", len(data))
        return data, 0.0, None, input_mime

    supported = suffix in (".mp3", ".wav")
    needs_trim = duration > max_s
    if supported and not needs_trim:
        return data, duration, None, input_mime

    out_suffix = _output_suffix(suffix)
    out_mime = _audio_mime(out_suffix)
    action = "trim" if needs_trim else "transcode"
    src_path: Optional[Path] = None
    dst_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
            src.write(data)
            src_path = Path(src.name)
        fd, dst_name = tempfile.mkstemp(suffix=out_suffix)
        os.close(fd)
        dst_path = Path(dst_name)

        # 同容器裁切优先 stream copy(几乎零 CPU);换容器或 copy 失败再重编码
        copy_ok = False
        if needs_trim and out_suffix == suffix:
            try:
                await _run_ffmpeg(
                    [
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-threads",
                        str(ffmpeg_thread_count()),
                        "-i",
                        str(src_path),
                        "-t",
                        f"{trim_target_s:.3f}",
                        "-map",
                        "0:a:0",
                        "-c:a",
                        "copy",
                        str(dst_path),
                    ]
                )
                copy_ok = bool(dst_path.stat().st_size)
            except VideoProcessError:
                copy_ok = False

        if not copy_ok:
            encode_args: list[str] = [
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-threads",
                str(ffmpeg_thread_count()),
                "-i",
                str(src_path),
            ]
            if needs_trim:
                encode_args += ["-t", f"{trim_target_s:.3f}"]
            encode_args += ["-map", "0:a:0", *_encode_args(out_suffix), str(dst_path)]
            await _run_ffmpeg(encode_args)

        out = dst_path.read_bytes()
        new_dur = await probe_audio_duration(out, suffix=out_suffix) or (trim_target_s if needs_trim else duration)
        logger.info(
            f"[audio_process] 参考音频处理 action={action} "
            f"{duration:.2f}s → {new_dur:.2f}s src={suffix} fmt={out_suffix} "
            f"size={len(data)}→{len(out)}"
        )
        return out, new_dur, action, out_mime
    except VideoProcessError as exc:
        logger.warning("[audio_process] 参考音频处理失败,放行原片: %s", exc)
        return data, duration, None, input_mime
    finally:
        for p in (src_path, dst_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


H3_REF_AUDIO_MIN_S = 2.0
H3_REF_AUDIO_MAX_S = 15.0
H3_REF_AUDIO_LOOP_TARGET_S = 2.5
H3_REF_AUDIO_TRIM_TARGET_S = 14.5


def _h3_audio_mime(data: bytes, fallback: str) -> str:
    if data.startswith(b"RIFF"):
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return fallback if fallback in ("audio/wav", "audio/mpeg") else "audio/mpeg"


async def _encode_h3_audio(
    src: Path,
    dst: Path,
    *,
    target_s: float,
    loop: bool,
    out_suffix: str,
) -> None:
    args: list[str] = [
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        str(ffmpeg_thread_count()),
    ]
    if loop:
        args += ["-stream_loop", "-1"]
    args += ["-i", str(src), "-t", f"{target_s:.3f}", "-map", "0:a:0"]
    if out_suffix == ".wav":
        args += ["-c:a", "pcm_s16le"]
    else:
        args += ["-c:a", "libmp3lame", "-b:a", "128k"]
    args.append(str(dst))
    await _run_ffmpeg(args)


async def clamp_minimax_h3_ref_audio(
    data: bytes,
    *,
    min_s: float = H3_REF_AUDIO_MIN_S,
    max_s: float = H3_REF_AUDIO_MAX_S,
    loop_target_s: float = H3_REF_AUDIO_LOOP_TARGET_S,
    trim_target_s: float = H3_REF_AUDIO_TRIM_TARGET_S,
    mime_type: str = "",
) -> tuple[bytes, float, Optional[str], str]:
    """H3 参考音频钳到 [min_s, max_s]:过短 loop,过长 trim。探不到时长返回 duration=0。"""
    fallback_mime = _h3_audio_mime(data, mime_type or "audio/mpeg")
    if not data:
        return data, 0.0, None, fallback_mime
    if not _which("ffmpeg") or not _which("ffprobe"):
        logger.warning("[audio_process] ffmpeg/ffprobe 不可用,跳过 H3 参考音频预处理")
        return data, 0.0, None, fallback_mime

    suffix = _guess_suffix(data, mime_type)
    duration = await probe_audio_duration(data, suffix=suffix)
    if duration <= 0:
        logger.warning("[audio_process] 无法探测 H3 参考音频时长 size=%d", len(data))
        return data, 0.0, None, fallback_mime
    if min_s <= duration <= max_s:
        return data, duration, None, fallback_mime

    need_loop = duration < min_s
    action = "loop" if need_loop else "trim"
    target_s = loop_target_s if need_loop else trim_target_s
    out_suffix = ".wav" if suffix == ".wav" else ".mp3"
    out_mime = "audio/wav" if out_suffix == ".wav" else "audio/mpeg"
    src_path: Optional[Path] = None
    dst_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
            src.write(data)
            src_path = Path(src.name)
        fd, dst_name = tempfile.mkstemp(suffix=out_suffix)
        os.close(fd)
        dst_path = Path(dst_name)

        copy_ok = False
        if not need_loop:
            try:
                await _run_ffmpeg(
                    [
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-threads",
                        str(ffmpeg_thread_count()),
                        "-i",
                        str(src_path),
                        "-t",
                        f"{target_s:.3f}",
                        "-map",
                        "0:a:0",
                        "-c:a",
                        "copy",
                        str(dst_path),
                    ]
                )
                copy_ok = bool(dst_path.stat().st_size)
            except VideoProcessError:
                copy_ok = False

        if not copy_ok:
            await _encode_h3_audio(
                src_path,
                dst_path,
                target_s=target_s,
                loop=need_loop,
                out_suffix=out_suffix,
            )

        out = dst_path.read_bytes()
        new_dur = await probe_audio_duration(out, suffix=out_suffix)
        if new_dur <= 0:
            new_dur = target_s
        logger.info(
            f"[audio_process] H3 参考音频预处理 action={action} "
            f"{duration:.2f}s → {new_dur:.2f}s fmt={out_suffix} "
            f"size={len(data)}→{len(out)}"
        )
        return out, new_dur, action, out_mime
    except VideoProcessError as exc:
        logger.warning("[audio_process] H3 参考音频预处理失败,放行原片: %s", exc)
        return data, duration, None, fallback_mime
    finally:
        for p in (src_path, dst_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = [
    "SEEDANCE_REF_AUDIO_MAX_S",
    "SEEDANCE_REF_AUDIO_TRIM_TARGET_S",
    "H3_REF_AUDIO_MIN_S",
    "H3_REF_AUDIO_MAX_S",
    "H3_REF_AUDIO_LOOP_TARGET_S",
    "H3_REF_AUDIO_TRIM_TARGET_S",
    "probe_audio_duration",
    "clamp_seedance_ref_audio",
    "clamp_minimax_h3_ref_audio",
]
