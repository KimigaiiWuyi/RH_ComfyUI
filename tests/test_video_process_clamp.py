"""Seedance 参考视频时长钳位 + 最低像素放大。

真实 ffmpeg 依赖:环境无 ffmpeg 时相关 case 自动 skip。
"""

from __future__ import annotations

import shutil
import asyncio

import pytest

from RH_ComfyUI.utils.video_process import (
    SEEDANCE_REF_VIDEO_MIN_PIXELS,
    SEEDANCE_REF_VIDEO_LOOP_TARGET_S,
    SEEDANCE_REF_VIDEO_TRIM_TARGET_S,
    RefVideoClampSpec,
    probe_video_meta,
    use_ref_video_clamp,
    probe_video_duration,
    clamp_seedance_ref_video,
    prepare_seedance_video_ref,
    prepare_ref_video_if_clamping,
    seedance_scale_for_min_pixels,
    plan_clip_durations_for_budget,
    seedance_ref_video_clamp_for_vendor,
)


def _has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _make_test_mp4(duration_s: float, *, size: str = "320x240") -> bytes:
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
                f"color=c=black:s={size}:d={duration_s:.3f}",
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


def test_scale_for_min_pixels_already_ok():
    assert seedance_scale_for_min_pixels(854, 480) == (854, 480)
    assert 854 * 480 >= SEEDANCE_REF_VIDEO_MIN_PIXELS


def test_scale_for_min_pixels_upsizes_small():
    # 640×360 = 230400 < 407696
    nw, nh = seedance_scale_for_min_pixels(640, 360)
    assert nw % 2 == 0 and nh % 2 == 0
    assert nw * nh >= SEEDANCE_REF_VIDEO_MIN_PIXELS
    assert abs(nw / nh - 640 / 360) < 0.03


def test_scale_for_min_pixels_tiny_square():
    nw, nh = seedance_scale_for_min_pixels(320, 240)
    assert nw % 2 == 0 and nh % 2 == 0
    assert nw * nh >= SEEDANCE_REF_VIDEO_MIN_PIXELS


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_probe_duration_of_generated_clip():
    data = _make_test_mp4(3.0)
    dur = asyncio.run(probe_video_duration(data))
    assert 2.5 <= dur <= 3.5


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_short_video_loops_to_target():
    data = _make_test_mp4(1.0, size="854x480")
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action == "loop"
    assert new_dur >= 2.0
    assert abs(new_dur - SEEDANCE_REF_VIDEO_LOOP_TARGET_S) < 0.6
    assert len(out) > 0


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_long_video_trims_to_target():
    data = _make_test_mp4(18.0, size="854x480")
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action == "trim"
    assert new_dur <= 15.0
    assert abs(new_dur - SEEDANCE_REF_VIDEO_TRIM_TARGET_S) < 0.6
    assert len(out) > 0


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_in_range_large_video_unchanged():
    data = _make_test_mp4(5.0, size="854x480")
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action is None
    assert out is data or out == data
    assert 4.5 <= new_dur <= 5.5


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_small_pixel_video_scales_up():
    data = _make_test_mp4(5.0, size="320x240")
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action == "scale"
    assert 4.5 <= new_dur <= 5.5
    _dur, w, h = asyncio.run(probe_video_meta(out))
    assert w * h >= SEEDANCE_REF_VIDEO_MIN_PIXELS


@pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_long_and_small_one_pass():
    data = _make_test_mp4(18.0, size="320x240")
    out, new_dur, action = asyncio.run(clamp_seedance_ref_video(data))
    assert action == "trim+scale"
    assert new_dur <= 15.0
    _dur, w, h = asyncio.run(probe_video_meta(out))
    assert w * h >= SEEDANCE_REF_VIDEO_MIN_PIXELS


def test_plan_clip_durations_within_budget_unchanged():
    out = plan_clip_durations_for_budget([14.5], budget_s=15.0, min_s=2.0, target_s=14.5)
    assert out == [14.5]
    two = plan_clip_durations_for_budget([5.0, 6.0], budget_s=15.0, min_s=2.0, target_s=14.5)
    assert two == [5.0, 6.0]


def test_plan_clip_durations_two_long_clips_share_budget():
    out = plan_clip_durations_for_budget([14.95, 14.95], budget_s=15.0, min_s=2.0, target_s=14.5)
    assert abs(sum(out) - 14.5) < 0.05
    assert all(2.0 <= d <= 14.95 for d in out)
    assert all(abs(d - 7.25) < 0.05 for d in out)


def test_plan_clip_durations_single_long_clip():
    out = plan_clip_durations_for_budget([30.0], budget_s=15.0, min_s=2.0, target_s=14.5)
    assert abs(out[0] - 14.5) < 1e-6


def test_seedance_ref_video_clamp_for_vendor():
    s20 = seedance_ref_video_clamp_for_vendor("doubao-seedance-2-0")
    assert s20.max_s == 15.0
    assert s20.trim_target_s == 14.5
    assert s20.min_pixels == SEEDANCE_REF_VIDEO_MIN_PIXELS
    s25 = seedance_ref_video_clamp_for_vendor("doubao-seedance-2.5")
    assert s25.max_s == 30.0
    assert s25.trim_target_s == 29.5
    ark25 = seedance_ref_video_clamp_for_vendor("doubao-seedance-2-5-pro-xxx")
    assert ark25.max_s == 30.0


def test_prepare_seedance_video_ref_clears_url_after_trim(monkeypatch):
    from RH_ComfyUI.utils.core.types import MediaRef, MediaKind

    async def _fake(data, **kwargs):
        assert kwargs.get("max_s") == 15.0
        return b"trimmed-mp4", 14.5, "trim"

    monkeypatch.setattr(
        "RH_ComfyUI.utils.video_process.prepare_seedance_ref_video",
        _fake,
    )
    ref = MediaRef(kind=MediaKind.VIDEO, data=b"LONG", url="https://cdn.example.com/long.mp4")
    out = asyncio.run(prepare_seedance_video_ref(ref, max_s=15.0, trim_target_s=14.5))
    assert out.url is None
    assert out.data == b"trimmed-mp4"
    assert out.mime_type == "video/mp4"


def test_prepare_seedance_video_ref_skips_asset_url():
    from RH_ComfyUI.utils.core.types import MediaRef, MediaKind

    ref = MediaRef(kind=MediaKind.VIDEO, url="asset://abc123")
    out = asyncio.run(prepare_seedance_video_ref(ref))
    assert out.url == "asset://abc123"


def test_prepare_ref_video_if_clamping_noop_without_context(monkeypatch):
    from RH_ComfyUI.utils.core.types import MediaRef, MediaKind

    called = {"n": 0}

    async def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("should not clamp without context")

    monkeypatch.setattr(
        "RH_ComfyUI.utils.video_process.prepare_seedance_video_ref",
        _boom,
    )
    ref = MediaRef(kind=MediaKind.VIDEO, data=b"VID", url="https://cdn.example.com/v.mp4")
    out = asyncio.run(prepare_ref_video_if_clamping(ref))
    assert out is ref
    assert called["n"] == 0


def test_prepare_ref_video_if_clamping_uses_context(monkeypatch):
    from RH_ComfyUI.utils.core.types import MediaRef, MediaKind

    seen: dict[str, float] = {}

    async def _fake(ref, **kwargs):
        seen["max_s"] = kwargs.get("max_s", 0)
        seen["min_pixels"] = kwargs.get("min_pixels", -1)
        return MediaRef(kind=MediaKind.VIDEO, data=b"TRIM", url=None, mime_type="video/mp4")

    monkeypatch.setattr(
        "RH_ComfyUI.utils.video_process.prepare_seedance_video_ref",
        _fake,
    )
    ref = MediaRef(kind=MediaKind.VIDEO, data=b"LONG")

    async def _go():
        with use_ref_video_clamp(RefVideoClampSpec(max_s=15.0, min_pixels=0)):
            return await prepare_ref_video_if_clamping(ref)

    out = asyncio.run(_go())
    assert out.data == b"TRIM"
    assert out.url is None
    assert seen["max_s"] == 15.0
    assert seen["min_pixels"] == 0

