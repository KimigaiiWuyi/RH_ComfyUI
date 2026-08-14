"""Seedance 参考音频时长裁切。"""

from __future__ import annotations

import os
import shutil
import asyncio
import tempfile
import subprocess

import pytest

from RH_ComfyUI.utils.audio_process import (
    SEEDANCE_REF_AUDIO_MAX_S,
    SEEDANCE_REF_AUDIO_TRIM_TARGET_S,
    probe_audio_duration,
    clamp_seedance_ref_audio,
)


def _has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_test_m4a(duration_s: float) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    fd, path = tempfile.mkstemp(suffix=".m4a")
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
                f"anullsrc=r=44100:cl=mono:d={duration_s:.3f}",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
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
def test_probe_audio_duration():
    data = _make_test_m4a(3.0)
    dur = asyncio.run(probe_audio_duration(data, suffix=".m4a"))
    assert 2.5 <= dur <= 3.5


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_short_audio_unchanged():
    data = _make_test_m4a(8.0)
    out, new_dur, action = asyncio.run(clamp_seedance_ref_audio(data))
    assert action is None
    assert out is data or out == data
    assert 7.5 <= new_dur <= 8.5


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_long_audio_trims():
    data = _make_test_m4a(20.0)
    out, new_dur, action = asyncio.run(clamp_seedance_ref_audio(data))
    assert action == "trim"
    assert new_dur <= SEEDANCE_REF_AUDIO_MAX_S
    assert abs(new_dur - SEEDANCE_REF_AUDIO_TRIM_TARGET_S) < 0.8
    assert len(out) > 0
