"""连线参考音在语音 normalize() 里提升成克隆端口。

画布计费前的 voice_refs 查不到 schema 时会静默跳过，mapper 又只读
reference_audio。这里用注册表里的真实模型走 normalize，确认兜底改写，
并且不盖掉调用方已经写好的音色。
"""

from __future__ import annotations

from RH_ComfyUI.core.base.speech import DigitalHumanSpeechBase
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest

_MODELS: dict[str, DigitalHumanSpeechBase] = {}


def _models() -> dict[str, DigitalHumanSpeechBase]:
    if _MODELS:
        return _MODELS
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.core.routing.registry import model_registry

    init_backends()
    discover_builtin_models()
    for name in ("s2.1-pro", "IndexTTS2.5", "minimax_t2a_speech", "mimo_tts"):
        model = model_registry.get(name)
        assert model is not None, name
        assert isinstance(model, DigitalHumanSpeechBase)
        _MODELS[name] = model
    return _MODELS


def _clip(marker: bytes) -> MediaRef:
    return MediaRef(kind=MediaKind.AUDIO, data=marker, mime_type="audio/wav")


def _req(
    prompt: str,
    *clips: MediaRef,
    reference_audio: bytes | None = None,
    reference_audios: list[bytes] | None = None,
) -> GenerationRequest:
    request = GenerationRequest(
        task_type=TaskType.SPEECH,
        prompt=prompt,
        audio_refs=list(clips),
        reference_audio=reference_audio,
    )
    if reference_audios is not None:
        request.reference_audios = reference_audios
    return request


def test_single_clip_is_adopted_without_mention() -> None:
    """连上参考音、正文不 @，也要克隆，不能落回内置默认音色。"""
    clip = _clip(b"voice-a")
    for name in ("IndexTTS2.5", "minimax_t2a_speech", "mimo_tts", "s2.1-pro"):
        out = _models()[name].normalize(_req("大家好", clip))
        assert out.reference_audio == b"voice-a", name
        assert not out.reference_audios, name
        assert out.prompt == "大家好", name
        assert out.audio_refs == [clip], name


def test_single_mention_is_stripped_and_does_not_emit_speaker_tag() -> None:
    """一名说话人时 Fish 会把标记当台词念出来，所以代号要删掉。"""
    clip = _clip(b"voice-a")
    out = _models()["s2.1-pro"].normalize(_req(" [@参考音频1] 大家好", clip))
    assert out.reference_audio == b"voice-a"
    assert not out.reference_audios
    assert out.prompt == "大家好"
    assert "speaker" not in out.prompt
    assert "参考音频" not in out.prompt


def test_fish_mentioned_clip_is_that_speaker_not_the_first() -> None:
    first, second = _clip(b"voice-a"), _clip(b"voice-b")
    out = _models()["s2.1-pro"].normalize(_req("[@参考音频2] 你好", first, second))
    assert out.reference_audio == b"voice-b"
    assert not out.reference_audios
    assert out.prompt == "你好"
    assert "<|speaker:" not in out.prompt


def test_non_fish_uses_the_first_clip_and_strips_the_mention() -> None:
    first, second = _clip(b"voice-a"), _clip(b"voice-b")
    out = _models()["IndexTTS2.5"].normalize(_req(" [@参考音频2] 你好", first, second))
    assert out.reference_audio == b"voice-a"
    assert out.prompt == "你好"
    assert out.audio_refs == [first, second]


def test_fish_two_mentions_become_speaker_tags() -> None:
    first, second = _clip(b"voice-a"), _clip(b"voice-b")
    out = _models()["s2.1-pro"].normalize(_req(" [@参考音频1] 你好 [@参考音频2] 我是 [@参考音频1] 再见", first, second))
    assert out.reference_audios == [b"voice-a", b"voice-b"]
    assert out.reference_audio == b"voice-a"
    assert out.prompt == "<|speaker:0|> 你好 <|speaker:1|> 我是 <|speaker:0|> 再见"
    assert "参考音频" not in out.prompt


def test_fish_out_of_range_mention_is_dropped() -> None:
    clip = _clip(b"voice-a")
    out = _models()["s2.1-pro"].normalize(_req(" [@参考音频9] 大家好", clip))
    assert out.reference_audio == b"voice-a"
    assert not out.reference_audios
    assert out.prompt == "大家好"


def test_existing_speaker_tags_fill_reference_audios_without_rewrite() -> None:
    first, second = _clip(b"voice-a"), _clip(b"voice-b")
    prompt = "<|speaker:0|> 你好 <|speaker:1|> 再见"
    out = _models()["s2.1-pro"].normalize(_req(prompt, first, second))
    assert out.reference_audios == [b"voice-a", b"voice-b"]
    assert out.reference_audio == b"voice-a"
    assert out.prompt == prompt


def test_explicit_reference_audio_is_not_overwritten() -> None:
    """已给的音色原样保留。正文代号也不改写成说话人标记。"""
    clip = _clip(b"voice-a")
    out = _models()["s2.1-pro"].normalize(_req(" [@参考音频1] 大家好", clip, reference_audio=b"explicit"))
    assert out.reference_audio == b"explicit"
    assert out.prompt == "[@参考音频1] 大家好"
    assert "<|speaker:" not in out.prompt
    assert out.audio_refs == [clip]


def test_explicit_reference_audios_are_not_overwritten() -> None:
    first, second = _clip(b"voice-a"), _clip(b"voice-b")
    explicit = [b"explicit-a", b"explicit-b"]
    out = _models()["s2.1-pro"].normalize(
        _req(" [@参考音频1] 大家好 [@参考音频2] 你好", first, second, reference_audios=explicit)
    )
    assert out.reference_audios == explicit
    assert out.reference_audio is None
    assert out.prompt == "[@参考音频1] 大家好 [@参考音频2] 你好"
    assert "<|speaker:" not in out.prompt


def test_url_only_ref_is_not_invented_into_a_voice() -> None:
    remote = MediaRef(kind=MediaKind.AUDIO, url="https://example.com/voice.mp3", mime_type="audio/mpeg")
    out = _models()["IndexTTS2.5"].normalize(_req("大家好", remote))
    assert out.reference_audio is None
    assert out.prompt == "大家好"


def test_model_without_voice_clone_does_not_adopt() -> None:
    model = _models()["IndexTTS2.5"]
    was_clone = model.supports_voice_clone
    model.supports_voice_clone = False
    try:
        clip = _clip(b"voice-a")
        out = model.normalize(_req(" [@参考音频1] 大家好", clip))
        assert out.reference_audio is None
        assert out.prompt == "[@参考音频1] 大家好"
    finally:
        model.supports_voice_clone = was_clone


def test_fish_adopt_runs_before_emotion_markers() -> None:
    clip = _clip(b"voice-a")
    out = _models()["s2.1-pro"].normalize(_req("<<EMO: 开心>> [@参考音频1] 你好", clip))
    assert out.reference_audio == b"voice-a"
    assert out.prompt == "[happy] 你好"
    assert out.mood is None


def test_adopt_is_idempotent() -> None:
    first, second = _clip(b"voice-a"), _clip(b"voice-b")
    fish = _models()["s2.1-pro"]
    once = fish.normalize(_req(" [@参考音频1] 你好 [@参考音频2] 我是", first, second))
    prompt_once = once.prompt
    audios_once = list(once.reference_audios)
    audio_once = once.reference_audio
    twice = fish.normalize(once)
    assert twice.prompt == prompt_once == "<|speaker:0|> 你好 <|speaker:1|> 我是"
    assert list(twice.reference_audios) == audios_once == [b"voice-a", b"voice-b"]
    assert twice.reference_audio == audio_once == b"voice-a"
