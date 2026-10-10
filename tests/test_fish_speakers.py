"""Fish Audio 多角色：reference_id 形态与情绪展开不吃掉 speaker 标记。"""

from __future__ import annotations

import pytest

from RH_ComfyUI.api import get_model_input_schema
from RH_ComfyUI.core.base.emotion import render_inline_markers
from RH_ComfyUI.rh_config.fish_models import FISH_TTS_MODELS
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


@pytest.mark.parametrize("spec", FISH_TTS_MODELS, ids=lambda s: s.name)
def test_fish_tts_schema_declares_reference_audios(spec):  # noqa: ANN001
    """多角色端口必须进 input_schema。

    外部插件靠它判断模型支不支持多角色；漏报会让它们退回
    单音色，用户连的参考音频被静默忽略。
    """
    schema = get_model_input_schema(spec.name)
    assert "reference_audio" in schema
    assert "reference_audios" in schema
