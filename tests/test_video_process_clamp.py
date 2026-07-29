"""Seedance 参考视频时长钳位单元测试。

真实 ffmpeg 依赖:环境无 ffmpeg 时相关 case 自动 skip。
"""

from __future__ import annotations

import shutil
import asyncio

import pytest

from RH_ComfyUI.utils.video_process import (
    SEEDANCE_REF_VIDEO_LOOP_TARGET_S,
    SEEDANCE_REF_VIDEO_TRIM_TARGET_S,
    probe_video_duration,
    clamp_seedance_ref_video,
)


def _has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_test_mp4(duration_s: float) -> bytes:
    """用 ffmpeg 生成一段纯色 silent mp4。"""
    import os
    import tempfile
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c=black:s=320x240:d={duration_s:.3f}",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=44100:cl=mono:d={duration_s:.3f}",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                path,
            ],
            check=True,
            timeout=60,
        )
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_probe_duration_of_generated_clip():
    data = _make_test_mp4(3.0)
    dur = asyncio.run(probe_video_duration(data))
    assert 2.5 <= dur <= 3.5


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_short_video_loops_to_target():
    data = _make_test_mp4(1.0)
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action == "loop"
    assert new_dur >= 2.0
    # 允许编码边界误差
    assert abs(new_dur - SEEDANCE_REF_VIDEO_LOOP_TARGET_S) < 0.6
    assert len(out) > 0


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_long_video_trims_to_target():
    data = _make_test_mp4(18.0)
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action == "trim"
    assert new_dur <= 15.0
    assert abs(new_dur - SEEDANCE_REF_VIDEO_TRIM_TARGET_S) < 0.6
    assert len(out) > 0


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_in_range_video_unchanged():
    data = _make_test_mp4(5.0)
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action is None
    assert out is data or out == data
    assert 4.5 <= new_dur <= 5.5
