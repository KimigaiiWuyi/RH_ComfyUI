"""媒体文件头优先于调用方 MIME。"""

from __future__ import annotations

from RH_ComfyUI.utils.core.types import MediaRef, MediaKind, sniff_media_kind

MP3 = b"ID3" + b"\x00" * 16
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 16
WAV = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 16
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_sniff_media_kind_prefers_unambiguous_bytes_over_mime():
    assert sniff_media_kind(data=WAV, mime="video/mp4") == MediaKind.AUDIO
    assert sniff_media_kind(data=MP3, mime="image/png") == MediaKind.AUDIO
    assert sniff_media_kind(data=PNG, mime="audio/mpeg") == MediaKind.IMAGE
    # ftyp 同时可能是 m4a / mp4,交给 MIME 判断
    assert sniff_media_kind(data=M4A, mime="audio/mp4") == MediaKind.AUDIO
    assert sniff_media_kind(data=M4A, mime="video/mp4") == MediaKind.VIDEO


def test_media_ref_mime_prefers_magic_bytes():
    mp3 = MediaRef(kind=MediaKind.AUDIO, data=MP3, mime_type="audio/mp4")
    assert mp3.mime_type == "audio/mpeg"

    m4a = MediaRef(kind=MediaKind.AUDIO, data=M4A, mime_type="audio/mpeg")
    assert m4a.mime_type == "audio/mp4"

    wav = MediaRef(kind=MediaKind.AUDIO, data=WAV, mime_type="audio/mpeg")
    assert wav.mime_type == "audio/wav"

    # ftyp 视频保留调用方给的 quicktime
    mov = MediaRef(kind=MediaKind.VIDEO, data=M4A, mime_type="video/quicktime")
    assert mov.mime_type == "video/quicktime"
