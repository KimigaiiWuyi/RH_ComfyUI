"""回归: 动态计费模型的 point_range.min 必须严格 < point_range.max。

前端 GenerationNode.tsx 用 `point_range.min < point_range.max` 判断是否要
请求 /api/RH_ComfyUI/models/estimate API。如果 min == max,前端当成"固定积分"
不会调 estimate,导致长文本输入时积分预览永远显示最小值(实际生成扣费可能
远超该值)。

历史 bug:IndexTTS2 / mimo_tts 的 point_range max 用 300 字符算出来的积分
仍然是 1(费率太低,900 bytes 不到 1M bytes 起征点),min == max = 1,
前端不调 estimate。
"""

from __future__ import annotations

import pytest

from RH_ComfyUI.models import discover_builtin_models
from RH_ComfyUI.utils.backends import init_backends
from RH_ComfyUI.utils.core.request import TaskType
from RH_ComfyUI.core.routing.registry import model_registry


@pytest.fixture(autouse=True)
def _setup():
    init_backends()
    discover_builtin_models()
    yield
    model_registry.clear()


def _all_models():
    """获取全部已注册模型(覆盖 speech/asr/image/video/music 全部 task_type)。"""
    return model_registry.all_models()


def test_all_speech_models_have_dynamic_point_range():
    """所有 speech 模型:point_range.min 必须严格 < max。"""
    speech_models = model_registry.by_modality(TaskType.SPEECH)
    assert speech_models, "应至少有一个 speech 模型"
    for m in speech_models:
        rmin, rmax = m.point_range()
        assert rmin < rmax, (
            f"{m.name} 的 point_range=({rmin}, {rmax}) min==max,"
            f"前端会判定为固定积分不调 estimate,实际动态计费预览会失真"
        )


def test_all_asr_models_have_dynamic_point_range():
    """所有 ASR 模型:同上。"""
    asr_models = model_registry.by_modality(TaskType.ASR)
    assert asr_models, "应至少有一个 ASR 模型"
    for m in asr_models:
        rmin, rmax = m.point_range()
        assert rmin < rmax, f"{m.name} 的 point_range=({rmin}, {rmax}) min==max,前端不会调 estimate"


def test_index_tts2_max_uses_realistic_text_length():
    """IndexTTS2 的 max 必须基于足够长的文本(否则费率再低也算不出差异)。

    锁死当前实现:5000 字符 × 3 bytes/字 = 15000 bytes × 500 积分/M bytes
    = 7.5 → 向上取整 8 积分。如果有人改回 300 字符,这个测试会失败,提醒
    "min==max → 前端不调 estimate"的回归。
    """
    from RH_ComfyUI.core.routing.registry import model_registry

    m = model_registry.get("IndexTTS2")
    assert m is not None
    rmin, rmax = m.point_range()
    assert rmin < rmax, f"IndexTTS2 应触发 estimate,实际 point_range=({rmin},{rmax})"
    assert rmin == 1, "min 应为空文本最小值"
    assert rmax >= 5, (
        f"IndexTTS2 max={rmax} 太小,可能 max 文本长度不够。前端 min==max 时不调 estimate,长文本用户看到积分不变。"
    )


def test_mimo_tts_max_uses_realistic_text_length():
    """mimo_tts 同 IndexTTS2 修复(同 rate 量级)。"""
    m = model_registry.get("mimo_tts")
    assert m is not None
    rmin, rmax = m.point_range()
    assert rmin < rmax, f"mimo_tts 应触发 estimate,实际 point_range=({rmin},{rmax})"
    assert rmax >= 5, f"mimo_tts max={rmax} 太小"


def test_fish_tts_point_range_still_dynamic():
    """回归保护:fish_tts 之前就是动态的(1, 2),确保修复 IndexTTS2 时没误改它。"""
    m = model_registry.get("fish_tts")
    assert m is not None
    rmin, rmax = m.point_range()
    assert rmin < rmax
    assert rmax >= 2, f"fish_tts max={rmax} 偏离预期(>=2)"
