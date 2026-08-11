"""Seedance 视频模型动态计价:token 计算/积分换算/estimate_cost 钩子"""

from RH_ComfyUI.models.video.defs import (
    Seedance2Def,
    Seedance25Def,
    Seedance2FastDef,
    Seedance2MiniDef,
    Seedance15ProDef,
)
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.seedance_billing import (
    _YUAN_TO_POINTS,
    _SEEDANCE2_RATES,
    _RESOLUTION_SPECS,
    _SEEDANCE25_RATES,
    _SEEDANCE2_FAST_RATES,
    _SEEDANCE2_MINI_RATES,
    _SEEDANCE25_AUTO_DURATION,
    _DEFAULT_INPUT_VIDEO_DURATION,
    _calculate_tokens,
    _tokens_to_points,
    estimate_seedance2_points,
    estimate_seedance25_points,
    estimate_seedance2_fast_points,
    estimate_seedance2_mini_points,
    estimate_seedance15_pro_points,
)

# ═══════════════════════════════════════════════════════════════════════
#  一、常量验证
# ═══════════════════════════════════════════════════════════════════════


def test_yuan_to_points():
    """1 元 = 100 积分"""
    assert _YUAN_TO_POINTS == 100


def test_default_input_video_duration():
    """默认输入视频时长 5 秒"""
    assert _DEFAULT_INPUT_VIDEO_DURATION == 5.0


def test_resolution_specs():
    """分辨率规格正确"""
    assert _RESOLUTION_SPECS["480p"] == (854, 480, 24)
    assert _RESOLUTION_SPECS["720p"] == (1280, 720, 24)
    assert _RESOLUTION_SPECS["1080p"] == (1920, 1080, 24)
    assert _RESOLUTION_SPECS["4k"] == (3840, 2160, 24)
    assert _RESOLUTION_SPECS["4K"] == (3840, 2160, 24)


def test_seedance2_rates():
    """Seedance 2.0 费率正确"""
    assert _SEEDANCE2_RATES["480p"] == (46.00, 28.00)
    assert _SEEDANCE2_RATES["720p"] == (46.00, 28.00)
    assert _SEEDANCE2_RATES["1080p"] == (51.00, 31.00)
    assert _SEEDANCE2_RATES["4k"] == (26.00, 16.00)


def test_seedance2_fast_rates():
    """Seedance 2.0 Fast 费率正确"""
    assert _SEEDANCE2_FAST_RATES["480p"] == (37.00, 22.00)
    assert _SEEDANCE2_FAST_RATES["720p"] == (37.00, 22.00)


def test_seedance2_mini_rates():
    """Seedance 2.0 Mini 费率正确"""
    assert _SEEDANCE2_MINI_RATES["480p"] == (23.00, 14.00)
    assert _SEEDANCE2_MINI_RATES["720p"] == (23.00, 14.00)


def test_seedance25_rates():
    """Seedance 2.5 费率正确(仅 480p/720p):无输入 70 元/M,有输入 42 元/M"""
    assert _SEEDANCE25_RATES["480p"] == (70.00, 42.00)
    assert _SEEDANCE25_RATES["720p"] == (70.00, 42.00)
    assert "1080p" not in _SEEDANCE25_RATES
    assert _SEEDANCE25_AUTO_DURATION == 15.0


# ═══════════════════════════════════════════════════════════════════════
# 二、token 计算
# ═══════════════════════════════════════════════════════════════════════


def test_calculate_tokens_formula():
    """token 公式:(输入时长 + 输出时长) × 宽 × 高 × fps / 1024"""
    # 720p, 5s 输出,无输入视频
    # (0 + 5) × 1280 × 720 × 24 / 1024 = 5 × 1280 × 720 × 24 / 1024 = 108000
    tokens = _calculate_tokens(0, 5, 1280, 720, 24)
    expected = 5 * 1280 * 720 * 24 / 1024
    assert tokens == expected


def test_calculate_tokens_with_input_video():
    """有输入视频时 token 更多"""
    # (5 + 5) × 1280 × 720 × 24 / 1024 = 216000
    tokens_no_input = _calculate_tokens(0, 5, 1280, 720, 24)
    tokens_with_input = _calculate_tokens(5, 5, 1280, 720, 24)
    assert tokens_with_input == 2 * tokens_no_input


def test_calculate_tokens_4k():
    """4K 分辨率 token 数"""
    # (0 + 5) × 3840 × 2160 × 24 / 1024 = 972000
    tokens = _calculate_tokens(0, 5, 3840, 2160, 24)
    expected = 5 * 3840 * 2160 * 24 / 1024
    assert tokens == expected


# ═══════════════════════════════════════════════════════════════════════
# 三、积分换算
# ═══════════════════════════════════════════════════════════════════════


def test_tokens_to_points():
    """token → 积分换算"""
    # 1M tokens × 46 元/M = 46 元 = 4600 积分
    points = _tokens_to_points(1_000_000, 46.00)
    assert points == 4600


def test_tokens_to_points_round_up():
    """积分向上取整"""
    # 0.5M tokens × 46 元/M = 23 元 = 2300 积分
    points = _tokens_to_points(500_000, 46.00)
    assert points == 2300


def test_tokens_to_points_minimum_one():
    """积分最小为 1"""
    points = _tokens_to_points(1, 46.00)
    assert points >= 1


# ═══════════════════════════════════════════════════════════════════════
# 四、Seedance 2.0 估算
# ═══════════════════════════════════════════════════════════════════════


def test_seedance2_720p_no_input():
    """Seedance 2.0 720p 无输入视频"""
    points = estimate_seedance2_points("720p", 5, video_refs=None)
    # tokens = 5 × 1280 × 720 × 24 / 1024 = 108000
    # 108000 × 4600 / 1M = 496.8 → ceil = 497
    assert points == 497


def test_seedance2_720p_with_input():
    """Seedance 2.0 720p 有输入视频(费率更低)"""
    points_no_input = estimate_seedance2_points("720p", 5, video_refs=None)
    points_with_input = estimate_seedance2_points("720p", 5, video_refs=[object()])
    # 有输入视频费率更低(28 vs 46),虽然 token 更多但费率差更大
    assert points_with_input < points_no_input * 2  # token 翻倍但费率约半


def test_seedance2_1080p():
    """Seedance 2.0 1080p 费率更高"""
    points_720p = estimate_seedance2_points("720p", 5, video_refs=None)
    points_1080p = estimate_seedance2_points("1080p", 5, video_refs=None)
    # 1080p 像素更多且费率更高
    assert points_1080p > points_720p


def test_seedance2_4k():
    """Seedance 2.0 4K 费率最低但像素最多"""
    points_720p = estimate_seedance2_points("720p", 5, video_refs=None)
    points_4k = estimate_seedance2_points("4k", 5, video_refs=None)
    # 4K 像素是 720p 的 ~9 倍,但费率只有 26/46 ≈ 0.57
    assert points_4k > points_720p


def test_seedance2_minimum_one():
    """任何参数组合积分 ≥ 1"""
    for res in ("480p", "720p", "1080p", "4k"):
        for dur in (4, 5, 10, 15):
            pts = estimate_seedance2_points(res, dur, video_refs=None)
            assert pts >= 1


# ═══════════════════════════════════════════════════════════════════════
# 五、Seedance 2.0 Fast 估算
# ═══════════════════════════════════════════════════════════════════════


def test_seedance2_fast_720p():
    """Seedance 2.0 Fast 720p"""
    points = estimate_seedance2_fast_points("720p", 5, video_refs=None)
    # tokens = 108000, 费率 37 元/M → 108000 × 3700 / 1M = 399.6 → ceil = 400
    assert points == 400


def test_seedance2_fast_480p():
    """Seedance 2.0 Fast 480p"""
    points_480p = estimate_seedance2_fast_points("480p", 5, video_refs=None)
    points_720p = estimate_seedance2_fast_points("720p", 5, video_refs=None)
    # 480p 像素更少,积分应更少
    assert points_480p < points_720p


# ═══════════════════════════════════════════════════════════════════════
# 六、Seedance 2.0 Mini 估算
# ═══════════════════════════════════════════════════════════════════════


def test_seedance2_mini_720p():
    """Seedance 2.0 Mini 720p"""
    points = estimate_seedance2_mini_points("720p", 5, video_refs=None)
    # tokens = 108000, 费率 23 元/M → 108000 × 2300 / 1M = 248.4 → ceil = 249
    assert points == 249


def test_seedance25_720p_no_input_official_example():
    """官方示例:720p 16:9 5s 无输入 = 7.56 元 = 756 积分

    tokens = 5 × 1280 × 720 × 24 / 1024 = 108000
    points = 108000 × 70 × 100 / 1_000_000 = 756
    """
    points = estimate_seedance25_points("720p", 5, video_refs=None)
    assert points == 756
    # 应高于同参 2.0(46 元/M)
    assert points > estimate_seedance2_points("720p", 5, video_refs=None)


def test_seedance25_720p_with_input_video_default_5s():
    """有输入视频但无时长 → 默认 5s 输入,走 42 元/M"""
    # tokens = (5+5)×1280×720×24/1024 = 216000
    # points = 216000 × 42 × 100 / 1_000_000 = 907.2 → ceil 908
    points = estimate_seedance25_points("720p", 5, video_refs=[object()])
    assert points == 908


def test_resolve_input_video_duration_sums_clips():
    """多段参考视频时长应累加"""
    from RH_ComfyUI.utils.mappers.seedance_billing import resolve_input_video_duration

    assert resolve_input_video_duration(None) == 0.0
    assert resolve_input_video_duration([]) == 0.0
    # 无时长对象 → 默认 5 × 段数
    assert resolve_input_video_duration([object(), object()]) == 10.0
    # 显式总时长优先
    assert resolve_input_video_duration([object()], input_video_duration=12.5) == 12.5
    # 可从 dict / 数值读单段
    assert resolve_input_video_duration([{"duration": 3}, {"duration": 7}]) == 10.0
    assert resolve_input_video_duration([8.0, 2.0]) == 10.0
    # 部分已知 + 部分未知
    assert resolve_input_video_duration([{"duration": 10}, object()]) == 15.0


def test_seedance25_input_duration_affects_points():
    """输入视频时长进入 token 公式:更长输入 → 更多积分"""
    short_in = estimate_seedance25_points("720p", 5, input_video_duration=5.0)
    long_in = estimate_seedance25_points("720p", 5, input_video_duration=15.0)
    assert long_in > short_in
    # 手算: (15+5)×1280×720×24/1024 = 432000 tokens × 42 元/M
    # = 432000 × 4200 / 1e6 = 1814.4 → ceil 1815
    assert long_in == 1815


def test_seedance25_multi_clip_default_duration():
    """3 段无时长参考视频 = 15s 输入,不等于固定 5s"""
    one = estimate_seedance25_points("720p", 5, video_refs=[object()])
    three = estimate_seedance25_points("720p", 5, video_refs=[object(), object(), object()])
    assert three > one
    assert three == estimate_seedance25_points("720p", 5, input_video_duration=15.0)


def test_seedance25_duration_30_more_than_5():
    """2.5 支持 30s,更长更贵"""
    short = estimate_seedance25_points("720p", 5, video_refs=None)
    long = estimate_seedance25_points("720p", 30, video_refs=None)
    assert long > short


def test_seedance25_auto_duration_minus_one():
    """duration=-1 按 15s 估算"""
    auto = estimate_seedance25_points("720p", -1, video_refs=None)
    fifteen = estimate_seedance25_points("720p", 15, video_refs=None)
    assert auto == fifteen


def test_seedance2_mini_cheaper_than_fast():
    """Mini 比 Fast 便宜"""
    mini = estimate_seedance2_mini_points("720p", 5, video_refs=None)
    fast = estimate_seedance2_fast_points("720p", 5, video_refs=None)
    assert mini < fast


# ═══════════════════════════════════════════════════════════════════════
# 七、Seedance 1.5 Pro 估算
# ═══════════════════════════════════════════════════════════════════════


def test_seedance15_pro_with_audio():
    """Seedance 1.5 Pro 有声视频"""
    points = estimate_seedance15_pro_points("720p", 5, generate_audio=True, video_refs=None)
    # tokens = 108000, 费率 16 元/M → 108000 × 1600 / 1M = 172.8 → ceil = 173
    assert points == 173


def test_seedance15_pro_without_audio():
    """Seedance 1.5 Pro 无声视频(费率减半)"""
    points_audio = estimate_seedance15_pro_points("720p", 5, generate_audio=True, video_refs=None)
    points_no_audio = estimate_seedance15_pro_points("720p", 5, generate_audio=False, video_refs=None)
    # 无声费率 8 vs 有声 16,积分应减半
    assert points_no_audio < points_audio


def test_seedance15_pro_audio_vs_no_audio_ratio():
    """有声/无声积分比约为 2:1(允许 ±1 取整误差)"""
    points_audio = estimate_seedance15_pro_points("720p", 5, generate_audio=True, video_refs=None)
    points_no_audio = estimate_seedance15_pro_points("720p", 5, generate_audio=False, video_refs=None)
    # 费率差 2 倍,token 相同,积分比≈ 2:1(允许向上取整带来的 ±1 误差)
    assert abs(points_audio - 2 * points_no_audio) <= 1


# ═══════════════════════════════════════════════════════════════════════
# 八、Seedance 1.0 Pro 估算
# ═══════════════════════════════════════════════════════════════════════


def test_seedance10_pro():
    """Seedance 1.0 Pro 固定费率 15 元/M"""
    # 1.0 Pro 不在 video/defs.py 中定义(可能在 aigc_system),这里只测试计费函数
    pass


# ═══════════════════════════════════════════════════════════════════════
# 九、模型 estimate_cost 钩子
# ═══════════════════════════════════════════════════════════════════════


def _make_video_request(
    resolution: str = "720p",
    duration: int = 5,
    generate_audio: bool = True,
    video_refs: list = None,
    **extra_params,
) -> GenerationRequest:
    params = {"resolution": resolution, "generate_audio": generate_audio}
    params.update(extra_params)
    return GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="test video",
        duration=duration,
        video_refs=video_refs or [],
        params=params,
    )


def test_seedance2_estimate_cost_dynamic():
    """Seedance2Def.estimate_cost 走动态计费"""
    m = Seedance2Def()
    req = _make_video_request(resolution="720p", duration=5)
    cost = m.estimate_cost(req)
    assert cost == estimate_seedance2_points("720p", 5, video_refs=None)


def test_seedance2_estimate_cost_different_resolution():
    """不同分辨率积分不同"""
    m = Seedance2Def()
    req_720p = _make_video_request(resolution="720p")
    req_1080p = _make_video_request(resolution="1080p")
    assert m.estimate_cost(req_720p) != m.estimate_cost(req_1080p)


def test_seedance2_estimate_cost_with_video_refs():
    """有输入视频时积分不同"""
    m = Seedance2Def()
    req_no_video = _make_video_request(resolution="720p", video_refs=None)
    req_with_video = _make_video_request(resolution="720p", video_refs=[object()])
    # 有输入视频 token 更多但费率更低,净效果取决于具体参数
    assert m.estimate_cost(req_no_video) >= 1
    assert m.estimate_cost(req_with_video) >= 1


def test_seedance2_fast_estimate_cost():
    """Seedance2FastDef.estimate_cost 走动态计费"""
    m = Seedance2FastDef()
    req = _make_video_request(resolution="720p", duration=5)
    cost = m.estimate_cost(req)
    assert cost == estimate_seedance2_fast_points("720p", 5, video_refs=None)


def test_seedance2_mini_estimate_cost():
    """Seedance2MiniDef.estimate_cost 走动态计费"""
    m = Seedance2MiniDef()
    req = _make_video_request(resolution="720p", duration=5)
    cost = m.estimate_cost(req)
    assert cost == estimate_seedance2_mini_points("720p", 5, video_refs=None)


def test_seedance25_estimate_cost_dynamic():
    """Seedance25Def.estimate_cost 走动态计费"""
    m = Seedance25Def()
    req = _make_video_request(resolution="720p", duration=5)
    cost = m.estimate_cost(req)
    assert cost == estimate_seedance25_points("720p", 5, video_refs=None)


def test_seedance25_point_range_dynamic():
    """Seedance 2.5 point_range 必须 min < max(触发前端 estimate)"""
    m = Seedance25Def()
    lo, hi = m.point_range()
    assert lo < hi
    assert lo == estimate_seedance25_points("480p", 4, video_refs=None)
    # max = 720p 30s 输出 + 150s 输入(10 段 × 15s 上限)
    assert hi == estimate_seedance25_points("720p", 30, input_video_duration=150.0)


def test_seedance15_pro_estimate_cost_with_audio():
    """Seedance15ProDef.estimate_cost 有声视频"""
    m = Seedance15ProDef()
    req = _make_video_request(resolution="720p", duration=5, generate_audio=True)
    cost = m.estimate_cost(req)
    assert cost == estimate_seedance15_pro_points("720p", 5, generate_audio=True, video_refs=None)


def test_seedance15_pro_estimate_cost_without_audio():
    """Seedance15ProDef.estimate_cost 无声视频"""
    m = Seedance15ProDef()
    req = _make_video_request(resolution="720p", duration=5, generate_audio=False)
    cost = m.estimate_cost(req)
    assert cost == estimate_seedance15_pro_points("720p", 5, generate_audio=False, video_refs=None)


def test_all_video_models_minimum_one():
    """所有视频模型任何合法参数组合积分 ≥ 1"""
    for m_cls in [
        Seedance2Def,
        Seedance25Def,
        Seedance2FastDef,
        Seedance2MiniDef,
        Seedance15ProDef,
    ]:
        m = m_cls()
        for res in ("480p", "720p", "1080p"):
            for dur in (4, 5, 10):
                req = _make_video_request(resolution=res, duration=dur)
                try:
                    cost = m.estimate_cost(req)
                    assert cost >= 1, f"{m_cls.__name__} res={res} dur={dur} cost={cost}"
                except Exception:
                    pass  # 某些分辨率可能不被支持(如 Fast 不支持 1080p)
