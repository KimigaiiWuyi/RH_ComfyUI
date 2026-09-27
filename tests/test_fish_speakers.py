"""Fish Audio 多角色：reference_id 形态与情绪展开不吃掉 speaker 标记。"""

from __future__ import annotations

from RH_ComfyUI.core.base.emotion import render_inline_markers
from RH_ComfyUI.utils.mappers.fishaudio_speech import fish_reference_id


def test_single_speaker_stays_a_string():
    assert fish_reference_id([]) is None
    assert fish_reference_id(["voice-a"]) == "voice-a"


def test_multi_speaker_is_an_ordered_list():
    assert fish_reference_id(["voice-a", "voice-b", "voice-a"]) == ["voice-a", "voice-b", "voice-a"]


def test_inline_emotion_keeps_speaker_tags():
    text = "<|speaker:0|><<EMO: 平静>>南方的雨。<|speaker:1|>我小时候在这里住过。"
    out = render_inline_markers(text)
    assert "<|speaker:0|>" in out
    assert "<|speaker:1|>" in out
    assert "[calm]" in out
    assert "<<EMO:" not in out
