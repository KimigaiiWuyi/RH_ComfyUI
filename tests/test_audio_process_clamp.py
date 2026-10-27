"""Seedance 参考音频时长裁切。"""

from __future__ import annotations

import os
import shutil
import asyncio
import tempfile
import subprocess

import pytest

from RH_ComfyUI.utils.audio_process import (
    H3_REF_AUDIO_MAX_S,
    H3_REF_AUDIO_MIN_S,
    SEEDANCE_REF_AUDIO_MAX_S,
    H3_REF_AUDIO_LOOP_TARGET_S,
    H3_REF_AUDIO_TRIM_TARGET_S,
    SEEDANCE_REF_AUDIO_TRIM_TARGET_S,
    _guess_suffix,
    probe_audio_duration,
    clamp_seedance_ref_audio,
    clamp_minimax_h3_ref_audio,
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


def _make_test_mp3(duration_s: float) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    fd, path = tempfile.mkstemp(suffix=".mp3")
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
                "libmp3lame",
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


def _make_test_wav(duration_s: float) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    fd, path = tempfile.mkstemp(suffix=".wav")
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
                "pcm_s16le",
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


def test_guess_suffix_prefers_magic_bytes_over_mime():
    mp3 = b"ID3" + b"\x00" * 16
    m4a = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 16
    wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 16
    aac = b"\xff\xf1" + b"\x00" * 16
    unknown = b"\x00" * 16

    assert _guess_suffix(mp3, "audio/mp4") == ".mp3"
    assert _guess_suffix(m4a, "audio/mpeg") == ".m4a"
    assert _guess_suffix(wav, "audio/mpeg") == ".wav"
    assert _guess_suffix(aac, "audio/mpeg") == ".aac"
    # 文件头识别不了时才回退调用方 MIME
    assert _guess_suffix(unknown, "audio/wav") == ".wav"


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_probe_audio_duration():
    data = _make_test_m4a(3.0)
    dur = asyncio.run(probe_audio_duration(data, suffix=".m4a"))
    assert 2.5 <= dur <= 3.5


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_short_audio_unchanged():
    data = _make_test_mp3(8.0)
    out, new_dur, action, mime = asyncio.run(clamp_seedance_ref_audio(data))
    assert action is None
    assert out is data or out == data
    assert 7.5 <= new_dur <= 8.5
    assert mime == "audio/mpeg"


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_short_m4a_transcodes_to_mp3():
    """Seedance 只收 wav/mp3:m4a 即使不超时也要转 mp3。"""
    data = _make_test_m4a(8.0)
    out, new_dur, action, mime = asyncio.run(clamp_seedance_ref_audio(data))
    assert action == "transcode"
    assert 7.5 <= new_dur <= 8.5
    assert mime == "audio/mpeg"
    assert out[:3] == b"ID3" or (out[0] == 0xFF and out[1] & 0xE0 == 0xE0)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_long_m4a_trims_and_converts_to_mp3():
    data = _make_test_m4a(20.0)
    out, new_dur, action, mime = asyncio.run(clamp_seedance_ref_audio(data))
    assert action == "trim"
    assert new_dur <= SEEDANCE_REF_AUDIO_MAX_S
    assert abs(new_dur - SEEDANCE_REF_AUDIO_TRIM_TARGET_S) < 0.8
    assert len(out) > 0
    assert mime == "audio/mpeg"
    assert out[:3] == b"ID3" or (out[0] == 0xFF and out[1] & 0xE0 == 0xE0)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_mp3_long_audio_trims_keeps_mp3():
    data = _make_test_mp3(40.0)
    out, new_dur, action, mime = asyncio.run(
        clamp_seedance_ref_audio(data, max_s=30.2, trim_target_s=30.0, mime_type="audio/mpeg")
    )
    assert action == "trim"
    assert 29.0 <= new_dur <= 30.5
    assert mime == "audio/mpeg"
    assert out[:3] == b"ID3" or (out[0] == 0xFF and out[1] & 0xE0 == 0xE0)


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_wav_long_audio_trims_keeps_wav():
    data = _make_test_wav(40.0)
    out, new_dur, action, mime = asyncio.run(
        clamp_seedance_ref_audio(data, max_s=30.2, trim_target_s=30.0, mime_type="audio/wav")
    )
    assert action == "trim"
    assert 29.0 <= new_dur <= 30.5
    assert mime == "audio/wav"
    assert out[:4] == b"RIFF"


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_h3_legal_audio_unchanged():
    data = _make_test_m4a(8.0)
    out, new_dur, action, mime = asyncio.run(clamp_minimax_h3_ref_audio(data))
    assert action is None
    assert out is data or out == data
    assert 7.5 <= new_dur <= 8.5
    assert mime


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_h3_short_audio_loops():
    data = _make_test_m4a(1.2)
    out, new_dur, action, _mime = asyncio.run(clamp_minimax_h3_ref_audio(data))
    assert action == "loop"
    assert new_dur >= H3_REF_AUDIO_MIN_S
    assert abs(new_dur - H3_REF_AUDIO_LOOP_TARGET_S) < 0.8
    assert len(out) > 0


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_h3_long_audio_trims():
    data = _make_test_m4a(20.0)
    out, new_dur, action, _mime = asyncio.run(clamp_minimax_h3_ref_audio(data))
    assert action == "trim"
    assert new_dur <= H3_REF_AUDIO_MAX_S
    assert abs(new_dur - H3_REF_AUDIO_TRIM_TARGET_S) < 0.8
    assert len(out) > 0
