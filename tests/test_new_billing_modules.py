"""新计费模块测试:ASR / TTS / Seedream5Pro"""

import pytest

from RH_ComfyUI.models.asr.defs import FishAsrDef
from RH_ComfyUI.models.image.defs import (
    Qwen2511Def,
    Qwen2512Def,
    Seedream5Def,
    Seedream5ProDef,
    MinimaxImage01Def,
)
from RH_ComfyUI.models.music.defs import AceStep15Def
from RH_ComfyUI.models.speech.defs import FishTtsDef, IndexTTS2Def
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.speech_billing import (
    FISHAUDIO_POINTS_PER_MILLION_BYTES,
    INDEX_TTS2_POINTS_PER_MILLION_BYTES,
    estimate_fish_tts_points,
    calculate_fish_tts_points,
    estimate_index_tts2_points,
    calculate_index_tts2_points,
)
from RH_ComfyUI.utils.mappers.fishaudio_asr_billing import (
    POINTS_PER_AUDIO_HOUR,
    POINTS_PER_AUDIO_SECOND,
    calculate_asr_points,
    estimate_fish_asr_points,
    estimate_audio_duration_seconds,
)
from RH_ComfyUI.utils.mappers.seedream5_pro_billing import (
    _SIZE_MODE_PIXELS,
    OUTPUT_COST_LOW_POINTS,
    OUTPUT_PIXEL_THRESHOLD,
    INPUT_IMAGE_COST_POINTS,
    OUTPUT_COST_HIGH_POINTS,
    estimate_seedream5_pro_points,
    calculate_seedream5_pro_points,
)

# ═══════════════════════════════════════════════════════════════════════
#  一、FishAudio ASR 计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_asr_points_per_audio_hour_constant():
    """0.36 美元 / 音频小时 → 36 积分 / 音频小时"""
    assert POINTS_PER_AUDIO_HOUR == 36


def test_asr_points_per_audio_second():
    """36 / 3600 = 0.01 积分/秒"""
    assert POINTS_PER_AUDIO_SECOND == pytest.approx(0.01)


def test_asr_estimate_duration_from_size():
    """按文件大小估算时长:128 kbps → 1MB ≈ 64 秒"""
    # 1MB = 1_048_576 bytes * 8 bits = 8_388_608 bits / 128_000 bps ≈ 65.5 秒
    fake_audio = b"\x00" * 1_048_576  # 1MB of silence
    duration = estimate_audio_duration_seconds(fake_audio)
    # 应该在 60-70 秒范围内(128 kbps)
    assert 50 < duration < 80


def test_asr_calculate_points_one_hour():
    """1 小时音频 = 36 积分"""
    # 模拟 1 小时音频:128 kbps * 3600 s = 57_600_000 bits = 7_200_000 bytes
    one_hour_bytes = int(128_000 / 8 * 3600)
    points = calculate_asr_points(b"\x00" * one_hour_bytes)
    # 36 积分(允许 ±1 误差,因估算有偏差)
    assert 30 <= points <= 40


def test_asr_minimum_one():
    """极短音频也至少 1 积分"""
    points = calculate_asr_points(b"\x00" * 100)
    assert points >= 1


def test_asr_text_returns_minimum():
    """无音频输入时返回 1 积分"""
    assert estimate_fish_asr_points(None) == 1
    assert estimate_fish_asr_points(b"") == 1


def test_asr_proportional():
    """音频越长积分越多"""
    # 128 kbps: 10_000 bytes ≈ 0.625s → 0.00625 积分 → 1
    # 1_000_000 bytes ≈ 62.5s → 0.625 积分 → 1
    # 需要用更长的音频才能体现差异
    short = calculate_asr_points(b"\x00" * 10_000)  # ≈ 0.625s → 1 积分
    long = calculate_asr_points(b"\x00" * 10_000_000)  # ≈ 625s → 6.25 → 7 积分
    assert short < long


# ── FishAsrDef.estimate_cost 钩子 ──


def _make_asr_request(audio: bytes = None, audio_refs=None) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.ASR,
        prompt="",
        audio_payload=audio,
        audio_refs=audio_refs or [],
    )


def test_fish_asr_estimate_cost_uses_audio_payload():
    """FishAsrDef.estimate_cost 使用 audio_payload 计算"""
    m = FishAsrDef()
    # 1秒音频 @ 128kbps = 16_000 bytes → 0.01 积分 → ceil = 1
    one_sec_bytes = 16_000
    req = _make_asr_request(audio=b"\x00" * one_sec_bytes)
    cost = m.estimate_cost(req)
    assert cost >= 1


def test_fish_asr_estimate_cost_with_audio_refs():
    """FishAsrDef.estimate_cost 支持 audio_refs[0].data"""
    m = FishAsrDef()
    from RH_ComfyUI.utils.core.types import MediaRef, MediaKind

    ref = MediaRef(kind=MediaKind.AUDIO, data=b"\x00" * 16_000, url=None, mime_type="audio/wav")
    req = _make_asr_request(audio_refs=[ref])
    cost = m.estimate_cost(req)
    assert cost >= 1


def test_fish_asr_estimate_cost_no_audio():
    """无音频输入时返回 1 积分"""
    m = FishAsrDef()
    req = _make_asr_request()
    assert m.estimate_cost(req) == 1


# ═══════════════════════════════════════════════════════════════════════
#  二、语音合成(TTS)计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_fish_tts_points_per_million_bytes_constant():
    """Fish Audio S2:15 美元 / M bytes → 1500 积分 / M bytes"""
    assert FISHAUDIO_POINTS_PER_MILLION_BYTES == 1500


def test_index_tts2_points_per_million_bytes_constant():
    """IndexTTS2:5 美元 / M bytes → 500 积分 / M bytes"""
    assert INDEX_TTS2_POINTS_PER_MILLION_BYTES == 500


def test_fish_tts_one_thousand_chars_chinese():
    """1000 中文字符 = 3000 UTF-8 字节 → 3000 * 1500 / 1M = 4.5 → ceil = 5"""
    text = "你好" * 500  # 1000 个中文字符
    points = calculate_fish_tts_points(text)
    byte_len = len(text.encode("utf-8"))
    assert byte_len == 3000
    # 3000 * 1500 / 1_000_000 = 4.5 → ceil = 5
    assert points == 5


def test_index_tts2_one_thousand_chars_chinese():
    """1000 中文字符 = 3000 UTF-8 字节 → 3000 * 500 / 1M = 1.5 → ceil = 2"""
    text = "你好" * 500
    points = calculate_index_tts2_points(text)
    # 3000 * 500 / 1_000_000 = 1.5 → ceil = 2
    assert points == 2


def test_fish_tts_english_text():
    """英文文本:1000 字符 = 1000 字节 → 1000 * 1500 / 1M = 1.5 → ceil = 2"""
    text = "hello " * 166 + "h"  # ~1000 字符
    byte_len = len(text.encode("utf-8"))
    assert byte_len == 997 or byte_len == 998 or byte_len == 999 or byte_len == 1000
    points = calculate_fish_tts_points(text)
    assert points == 2


def test_tts_empty_text():
    """空文本返回 1 积分"""
    assert calculate_fish_tts_points("") == 1
    assert calculate_fish_tts_points(None) == 1
    assert calculate_index_tts2_points("") == 1
    assert calculate_index_tts2_points(None) == 1


def test_tts_proportional():
    """文本越长积分越多"""
    short = calculate_fish_tts_points("你好")
    long = calculate_fish_tts_points("你好" * 1000)
    assert short < long


def test_tts_minimum_one():
    """任何合法参数组合积分 ≥ 1"""
    assert calculate_fish_tts_points("a") >= 1
    assert calculate_index_tts2_points("a") >= 1


# ── TTS 模型 estimate_cost 钩子 ──


def _make_speech_request(prompt: str = "你好") -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.SPEECH, prompt=prompt)


def test_fish_tts_estimate_cost_dynamic():
    """FishTtsDef.estimate_cost 走动态计费"""
    m = FishTtsDef()
    # 短文本
    req_short = _make_speech_request("你好")
    cost_short = m.estimate_cost(req_short)
    assert cost_short == estimate_fish_tts_points("你好")

    # 长文本应比短文本贵
    req_long = _make_speech_request("你好" * 1000)
    cost_long = m.estimate_cost(req_long)
    assert cost_long > cost_short


def test_index_tts2_estimate_cost_dynamic():
    """IndexTTS2Def.estimate_cost 走动态计费"""
    m = IndexTTS2Def()
    req = _make_speech_request("你好" * 500)
    assert m.estimate_cost(req) == estimate_index_tts2_points("你好" * 500)


def test_fish_tts_estimate_cost_matches_direct():
    """estimate_cost 与直接调用计费函数一致"""
    m = FishTtsDef()
    for text in ["你好", "hello world", "这是一段测试文本" * 100]:
        req = _make_speech_request(text)
        assert m.estimate_cost(req) == estimate_fish_tts_points(text)


# ═══════════════════════════════════════════════════════════════════════
#  三、Seedream5Pro 计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_seedream5_pro_constants():
    """计费常量正确"""
    assert INPUT_IMAGE_COST_POINTS == 2
    assert OUTPUT_COST_LOW_POINTS == 30
    assert OUTPUT_COST_HIGH_POINTS == 60
    assert OUTPUT_PIXEL_THRESHOLD == 2_360_000


def test_seedream5_pro_size_mode_pixels():
    """分辨率档位像素数正确"""
    assert _SIZE_MODE_PIXELS["1K"] == 1024 * 1024  # 1_048_576 ≤ 236 万
    assert _SIZE_MODE_PIXELS["2K"] == 2048 * 2048  # 4_194_304 > 236 万


@pytest.mark.parametrize(
    "num_input,size_mode,expected",
    [
        (0, "1K", 30),  # 0 输入 + 1K 输出(30)
        (0, "2K", 60),  # 0 输入 + 2K 输出(60)
        (1, "1K", 30),  # 1 输入(免费) + 1K 输出(30) = 30
        (1, "2K", 60),  # 1 输入(免费) + 2K 输出(60) = 60
        (2, "1K", 32),  # 2 输入(1*2=2) + 1K 输出(30) = 32
        (2, "2K", 62),  # 2 输入(2) + 2K 输出(60) = 62
        (5, "1K", 38),  # 5 输入(4*2=8) + 1K 输出(30) = 38
        (10, "2K", 78),  # 10 输入(9*2=18) + 2K 输出(60) = 78
    ],
)
def test_seedream5_pro_calculate_points(num_input, size_mode, expected):
    """各组合积分计算正确"""
    assert calculate_seedream5_pro_points(num_input, size_mode) == expected


def test_seedream5_pro_default_size():
    """size_mode 缺失 → 按 2K 档估算"""
    assert calculate_seedream5_pro_points(0, None) == calculate_seedream5_pro_points(0, "2K")


def test_seedream5_pro_invalid_size_fallback():
    """非法档位 → 回落到 2K 档"""
    assert calculate_seedream5_pro_points(0, "8K") == calculate_seedream5_pro_points(0, "2K")


def test_seedream5_pro_first_image_free():
    """首张输入图免费"""
    assert calculate_seedream5_pro_points(1, "1K") == 30  # 不加输入费
    assert calculate_seedream5_pro_points(0, "1K") == 30  # 相同


def test_seedream5_pro_minimum_one():
    """任何合法参数组合积分 ≥ 1"""
    for n in (0, 1, 2, 5, 10):
        for sz in ("1K", "2K"):
            assert calculate_seedream5_pro_points(n, sz) >= 1


# ── Seedream5ProDef.estimate_cost 钩子 ──


def _make_image_request(images=None, **params) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="test",
        images=images or [],
        params=params,
    )


def test_seedream5_pro_estimate_cost_dynamic():
    """Seedream5ProDef.estimate_cost 走动态计费"""
    m = Seedream5ProDef()
    # 无输入 + 2K = 60 积分
    req_default = _make_image_request(size_mode="2K")
    cost_default = m.estimate_cost(req_default)
    assert cost_default == estimate_seedream5_pro_points(0, "2K") == 60

    # 多输入 + 1K = (3-1)*2 + 30 = 34 积分
    req_multi = _make_image_request(images=[b"img1", b"img2", b"img3"], size_mode="1K")
    cost_multi = m.estimate_cost(req_multi)
    assert cost_multi == estimate_seedream5_pro_points(3, "1K") == 34

    # 相同分辨率下,有输入应比无输入贵
    req_no_input_1k = _make_image_request(size_mode="1K")
    req_with_input_1k = _make_image_request(images=[b"img1", b"img2"], size_mode="1K")
    assert m.estimate_cost(req_with_input_1k) > m.estimate_cost(req_no_input_1k)


def test_seedream5_pro_estimate_cost_matches_direct():
    """estimate_cost 与直接调用计费函数一致"""
    m = Seedream5ProDef()
    for n in (0, 1, 3, 5):
        for sz in ("1K", "2K"):
            imgs = [b"x"] * n
            req = _make_image_request(images=imgs, size_mode=sz)
            assert m.estimate_cost(req) == estimate_seedream5_pro_points(n, sz)


# ═══════════════════════════════════════════════════════════════════════
#  四、固定价格模型测试
# ═══════════════════════════════════════════════════════════════════════


def _make_music_request() -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.MUSIC, prompt="test")


def _make_qwen_request() -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.IMAGE, prompt="test", images=[b"img"])


def test_ace_step15_fixed_10():
    """ACE Step 1.5 固定 10 积分"""
    m = AceStep15Def()
    req = _make_music_request()
    assert m.estimate_cost(req) == 10


def test_qwen_2511_fixed_15():
    """Qwen-Edit 2511 固定 15 积分"""
    m = Qwen2511Def()
    req = _make_qwen_request()
    assert m.estimate_cost(req) == 15


def test_qwen_2512_fixed_15():
    """Qwen-Image 2512 固定 15 积分"""
    m = Qwen2512Def()
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="test")
    assert m.estimate_cost(req) == 15


def test_minimax_image01_fixed_3():
    """MiniMax Image-01 固定 3 积分(point_cost=3,静态)"""
    m = MinimaxImage01Def()
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="test")
    assert m.estimate_cost(req) == 3


def test_seedream5_fixed_22():
    """Seedream5 Lite 固定 22 积分(point_cost=22,静态)"""
    m = Seedream5Def()
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="test")
    assert m.estimate_cost(req) == 22
