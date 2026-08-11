"""额外模型计费测试:Wan2.2 / MiMo TTS / MiniMax T2A Speech"""

from RH_ComfyUI.models.video.defs import Wan22VideogenDef
from RH_ComfyUI.models.speech.defs import MimoTtsDef, MinimaxT2aSpeechDef
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.extra_billing import (
    WAN22_POINTS_PER_SECOND,
    MIMO_POINTS_PER_MILLION_BYTES,
    MINIMAX_T2A_POINTS_PER_10K_CHARS,
    estimate_wan22_points,
    calculate_wan22_points,
    estimate_mimo_tts_points,
    calculate_mimo_tts_points,
    estimate_minimax_t2a_points,
    calculate_minimax_t2a_points,
)

# ═══════════════════════════════════════════════════════════════════════
#  一、Wan2.2 视频生成计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_wan22_points_per_second():
    """Wan2.2: 0.6 元/秒 = 60 积分/秒"""
    assert WAN22_POINTS_PER_SECOND == 60


def test_wan22_calculate_points():
    """按时长计费"""
    assert calculate_wan22_points(1) == 60
    assert calculate_wan22_points(5) == 300
    assert calculate_wan22_points(10) == 600


def test_wan22_minimum_one():
    """最小时长也至少 1 积分"""
    assert calculate_wan22_points(0) == 1
    assert calculate_wan22_points(-1) == 1


def test_wan22_estimate():
    """estimate 与 calculate 一致"""
    assert estimate_wan22_points(5) == calculate_wan22_points(5)


# ── Wan22VideogenDef.estimate_cost 钩子 ──


def _make_wan22_request(duration: int = 5) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="test video",
        duration=duration,
    )


def test_wan22_estimate_cost():
    """Wan22VideogenDef.estimate_cost 走动态计费"""
    m = Wan22VideogenDef()
    req = _make_wan22_request(duration=5)
    assert m.estimate_cost(req) == 300  # 5 × 60


def test_wan22_estimate_cost_proportional():
    """时长越长积分越多"""
    m = Wan22VideogenDef()
    req_short = _make_wan22_request(duration=3)
    req_long = _make_wan22_request(duration=10)
    assert m.estimate_cost(req_short) < m.estimate_cost(req_long)


# ═══════════════════════════════════════════════════════════════════════
#  二、MiMo TTS 计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_mimo_points_per_million_bytes():
    """MiMo TTS: 6 美元/M bytes → 600 积分/M bytes"""
    assert MIMO_POINTS_PER_MILLION_BYTES == 600


def test_mimo_1000_chinese_chars():
    """1000 中文字符 = 3000 UTF-8 字节 → 3000 × 600 / 1M = 1.8 → ceil = 2"""
    text = "你好" * 500
    points = calculate_mimo_tts_points(text)
    assert points == 2


def test_mimo_empty_text():
    """空文本返回 1 积分"""
    assert calculate_mimo_tts_points("") == 1
    assert calculate_mimo_tts_points(None) == 1


def test_mimo_proportional():
    """文本越长积分越多"""
    short = calculate_mimo_tts_points("你好")
    long = calculate_mimo_tts_points("你好" * 1000)
    assert short < long


# ── MimoTtsDef.estimate_cost 钩子 ──


def _make_speech_request(prompt: str = "你好") -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.SPEECH, prompt=prompt)


def test_mimo_estimate_cost():
    """MimoTtsDef.estimate_cost 走动态计费"""
    m = MimoTtsDef()
    req = _make_speech_request("你好" * 500)
    assert m.estimate_cost(req) == estimate_mimo_tts_points("你好" * 500)


# ═══════════════════════════════════════════════════════════════════════
#  三、MiniMax T2A Speech 计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_minimax_t2a_points_per_10k_chars():
    """MiniMax T2A: 3.5 元/万字符 = 350 积分/万字符"""
    assert MINIMAX_T2A_POINTS_PER_10K_CHARS == 350


def test_minimax_t2a_10000_chars():
    """10000 字符 = 350 积分"""
    text = "a" * 10000
    points = calculate_minimax_t2a_points(text)
    assert points == 350


def test_minimax_t2a_chinese():
    """中文按字符计费(每字 1 字符)"""
    text = "你好" * 5000  # 10000 字符
    points = calculate_minimax_t2a_points(text)
    assert points == 350


def test_minimax_t2a_empty():
    """空文本返回 1 积分"""
    assert calculate_minimax_t2a_points("") == 1
    assert calculate_minimax_t2a_points(None) == 1


def test_minimax_t2a_proportional():
    """字符越多积分越多"""
    short = calculate_minimax_t2a_points("你好")
    long = calculate_minimax_t2a_points("你好" * 10000)
    assert short < long


# ── MinimaxT2aSpeechDef.estimate_cost 钩子 ──


def test_minimax_t2a_estimate_cost():
    """MinimaxT2aSpeechDef.estimate_cost 走动态计费"""
    m = MinimaxT2aSpeechDef()
    req = _make_speech_request("你好" * 5000)
    assert m.estimate_cost(req) == estimate_minimax_t2a_points("你好" * 5000)


# ═══════════════════════════════════════════════════════════════════════
#  四、综合测试
# ═══════════════════════════════════════════════════════════════════════


def test_all_models_minimum_one():
    """所有模型任何合法参数组合积分 ≥ 1"""
    # Wan22
    assert calculate_wan22_points(0) >= 1
    assert calculate_wan22_points(0.01) >= 1
    # MiMo
    assert calculate_mimo_tts_points("") >= 1
    assert calculate_mimo_tts_points("a") >= 1
    # MiniMax
    assert calculate_minimax_t2a_points("") >= 1
    assert calculate_minimax_t2a_points("a") >= 1
