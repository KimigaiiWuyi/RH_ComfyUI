"""Nano Banana 1/2/Pro 动态计价:token 计算/积分换算/estimate_cost 钩子"""

import pytest

from RH_ComfyUI.utils.mappers.nanobanana2_billing import (
    POINTS_PER_MILLION_TOKENS as NB2_POINTS_PER_MILLION_TOKENS,
    OUTPUT_TOKENS_BY_SIZE as NB2_OUTPUT_TOKENS_BY_SIZE,
    calculate_output_points as nb2_calculate_output_points,
    estimate_nanobanana2_points,
)
from RH_ComfyUI.utils.mappers.nanobanana1_billing import (
    POINTS_PER_MILLION_TOKENS as NB1_POINTS_PER_MILLION_TOKENS,
    OUTPUT_TOKENS as NB1_OUTPUT_TOKENS,
    calculate_output_points as nb1_calculate_output_points,
    estimate_nanobanana1_points,
)
from RH_ComfyUI.utils.mappers.banana_pro_billing import (
    OUTPUT_POINTS_PER_MILLION_TOKENS as BPRO_OUTPUT_POINTS_PER_MILLION_TOKENS,
    INPUT_COST_PER_IMAGE_POINTS as BPRO_INPUT_COST_PER_IMAGE_POINTS,
    OUTPUT_TOKENS_BY_SIZE as BPRO_OUTPUT_TOKENS_BY_SIZE,
    calculate_banana_pro_points,
    estimate_banana_pro_points,
)
from RH_ComfyUI.models.image.defs import Banana2Def, Banana1Def, BananaProDef
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest


# ═══════════════════════════════════════════════════════════════════════
#  一、Nano Banana 2 计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_nb2_points_per_million_tokens_constant():
    """60 美元 / 1M tokens,1 美元 = 100 积分 → 6_000 积分 / 1M tokens"""
    assert NB2_POINTS_PER_MILLION_TOKENS == 6_000


@pytest.mark.parametrize(
    "image_size,expected_tokens",
    [
        ("512", 747),
        ("1K", 1120),
        ("2K", 1680),
        ("4K", 2520),
    ],
)
def test_nb2_output_tokens_by_size(image_size, expected_tokens):
    """各分辨率档位 token 消耗与官方文档一致"""
    assert NB2_OUTPUT_TOKENS_BY_SIZE[image_size] == expected_tokens


@pytest.mark.parametrize(
    "image_size,expected_points",
    [
        ("512", 5),    # 747 * 6000 / 1M = 4.482 → ceil = 5
        ("1K", 7),     # 1120 * 6000 / 1M = 6.72 → ceil = 7
        ("2K", 11),    # 1680 * 6000 / 1M = 10.08 → ceil = 11
        ("4K", 16),    # 2520 * 6000 / 1M = 15.12 → ceil = 16
    ],
)
def test_nb2_calculate_output_points(image_size, expected_points):
    """各分辨率档位积分换算正确(向上取整)"""
    assert nb2_calculate_output_points(image_size) == expected_points


def test_nb2_default_size():
    """image_size 缺失 → 按 2K 档估算"""
    assert nb2_calculate_output_points(None) == nb2_calculate_output_points("2K")


def test_nb2_invalid_size_fallback():
    """非法档位 → 回落到 2K 档"""
    assert nb2_calculate_output_points("8K") == nb2_calculate_output_points("2K")


def test_nb2_size_ordering():
    """分辨率越高积分越高"""
    p512 = nb2_calculate_output_points("512")
    p1k = nb2_calculate_output_points("1K")
    p2k = nb2_calculate_output_points("2K")
    p4k = nb2_calculate_output_points("4K")
    assert p512 < p1k < p2k < p4k


def test_nb2_minimum_one():
    """任何合法参数组合积分 ≥ 1"""
    for sz in ("512", "1K", "2K", "4K"):
        assert nb2_calculate_output_points(sz) >= 1


# ── Banana2Def.estimate_cost 钩子 ──


def _make_request(ratio=None, images=None, **params) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="test",
        ratio=ratio,
        images=images or [],
        params=params,
    )


def test_banana2_estimate_cost_dynamic():
    """Banana2Def.estimate_cost 走动态计费,按 image_size 分档"""
    m = Banana2Def()
    # 默认参数(无 params) → 2K 档
    req_default = _make_request()
    cost_default = m.estimate_cost(req_default)
    assert cost_default == estimate_nanobanana2_points("2K")

    # 4K 应比默认贵
    req_4k = _make_request(image_size="4K")
    assert m.estimate_cost(req_4k) > cost_default

    # 512 应比默认便宜
    req_512 = _make_request(image_size="512")
    assert m.estimate_cost(req_512) < cost_default


def test_banana2_estimate_cost_matches_direct():
    """各档位 estimate_cost 与直接调用计费函数一致"""
    m = Banana2Def()
    for sz in ("512", "1K", "2K", "4K"):
        req = _make_request(image_size=sz)
        assert m.estimate_cost(req) == estimate_nanobanana2_points(sz)


def test_banana2_estimate_cost_never_below_minimum():
    """任何合法参数组合积分 ≥ 1"""
    m = Banana2Def()
    for sz in ("512", "1K", "2K", "4K", None, "invalid"):
        req = _make_request(image_size=sz)
        assert m.estimate_cost(req) >= 1


# ═══════════════════════════════════════════════════════════════════════
#  二、Nano Banana 1 计费测试
# ═══════════════════════════════════════════════════════════════════════


def test_nb1_points_per_million_tokens_constant():
    """30 美元 / 1M tokens,1 美元 = 100 积分 → 3_000 积分 / 1M tokens"""
    assert NB1_POINTS_PER_MILLION_TOKENS == 3_000


def test_nb1_output_tokens():
    """一代模型固定 1290 tokens"""
    assert NB1_OUTPUT_TOKENS == 1290


def test_nb1_calculate_output_points():
    """1290 * 3000 / 1M = 3.87 → ceil = 4"""
    assert nb1_calculate_output_points() == 4


def test_nb1_estimate_points():
    """estimate_nanobanana1_points 返回固定值"""
    assert estimate_nanobanana1_points() == 4


# ── Banana1Def.estimate_cost 钩子 ──


def test_banana1_estimate_cost_fixed():
    """Banana1Def.estimate_cost 返回固定值(一代无尺寸档位)"""
    m = Banana1Def()
    req = _make_request()
    assert m.estimate_cost(req) == 4


def test_banana1_estimate_cost_same_regardless_of_params():
    """一代模型无论参数如何,积分固定"""
    m = Banana1Def()
    req1 = _make_request(ratio="1:1")
    req2 = _make_request(ratio="16:9")
    assert m.estimate_cost(req1) == m.estimate_cost(req2) == 4


# ═══════════════════════════════════════════════════════════════════════
#  三、Banana Pro 计费测试(独立于 GPT-Image-2)
# ═══════════════════════════════════════════════════════════════════════


def test_bpro_output_points_per_million_tokens_constant():
    """120 美元 / 1M tokens,1 美元 = 100 积分 → 12_000 积分 / 1M tokens"""
    assert BPRO_OUTPUT_POINTS_PER_MILLION_TOKENS == 12_000


def test_bpro_input_cost_per_image():
    """每张输入图片 0.0011 美元 = 0.11 积分"""
    assert BPRO_INPUT_COST_PER_IMAGE_POINTS == pytest.approx(0.11)


@pytest.mark.parametrize(
    "image_size,expected_tokens",
    [
        ("1K", 1120),
        ("2K", 1120),
        ("4K", 2000),
    ],
)
def test_bpro_output_tokens_by_size(image_size, expected_tokens):
    """各分辨率档位 token 消耗与官方文档一致"""
    assert BPRO_OUTPUT_TOKENS_BY_SIZE[image_size] == expected_tokens


@pytest.mark.parametrize(
    "num_input,image_size,expected",
    [
        (0, "1K", 14),    # 输入 0 + 输出 1K(1120*12000/1M=13.44 → ceil=14) = 14
        (0, "2K", 14),    # 同上
        (0, "4K", 24),    # 输入 0 + 输出 4K(2000*12000/1M=24.0 → ceil=24) = 24
        (1, "1K", 15),    # 输入 1(ceil(0.11)=1) + 输出 14 = 15
        (1, "4K", 25),    # 输入 1 + 输出 24 = 25
        (3, "2K", 15),    # 输入 3(ceil(0.33)=1) + 输出 14 = 15
        (3, "4K", 25),    # 输入 3 + 输出 24 = 25
    ],
)
def test_bpro_calculate_points(num_input, image_size, expected):
    """输入 + 输出积分合计正确"""
    assert calculate_banana_pro_points(num_input, image_size) == expected


def test_bpro_default_size():
    """image_size 缺失 → 按 2K 档估算"""
    assert calculate_banana_pro_points(0, None) == calculate_banana_pro_points(0, "2K")


def test_bpro_invalid_size_fallback():
    """非法档位 → 回落到 2K 档"""
    assert calculate_banana_pro_points(0, "8K") == calculate_banana_pro_points(0, "2K")


def test_bpro_size_ordering():
    """同输入下,分辨率越高积分越高"""
    p1k = calculate_banana_pro_points(0, "1K")
    p2k = calculate_banana_pro_points(0, "2K")
    p4k = calculate_banana_pro_points(0, "4K")
    assert p1k == p2k  # 1K 和 2K 同档
    assert p2k < p4k


def test_bpro_input_surcharge():
    """有输入图片时应比无输入贵(或相等,因输入费 < 1 积分取整)"""
    no_input = calculate_banana_pro_points(0, "1K")
    with_input = calculate_banana_pro_points(1, "1K")
    assert with_input >= no_input


def test_bpro_minimum_one():
    """任何合法参数组合积分 ≥ 1"""
    for n in (0, 1, 2, 3):
        for sz in ("1K", "2K", "4K"):
            assert calculate_banana_pro_points(n, sz) >= 1


# ── BananaProDef.estimate_cost 钩子 ──


def test_banana_pro_estimate_cost_dynamic():
    """BananaProDef.estimate_cost 走独立动态计费(不再与 gpt-image-2 共享)"""
    m = BananaProDef()
    # 默认参数(无 params,无 images) → 0 输入 + 2K 档
    req_default = _make_request()
    cost_default = m.estimate_cost(req_default)
    assert cost_default == estimate_banana_pro_points(0, "2K")

    # 4K 应比默认贵
    req_4k = _make_request(image_size="4K")
    assert m.estimate_cost(req_4k) > cost_default

    # 有输入图片应比无输入贵(或相等)
    req_with_img = _make_request(images=[b"fake_image_data"])
    assert m.estimate_cost(req_with_img) >= cost_default


def test_banana_pro_estimate_cost_with_input_images():
    """有输入图片时积分包含输入计费"""
    m = BananaProDef()
    req = _make_request(images=[b"img1", b"img2"], image_size="4K")
    expected = estimate_banana_pro_points(2, "4K")
    assert m.estimate_cost(req) == expected


def test_banana_pro_estimate_cost_no_longer_matches_gpt_image2():
    """BananaProDef 不再与 GptImage2Def 共享计费逻辑"""
    from RH_ComfyUI.utils.mappers.gpt_image2_billing import estimate_gpt_image2_points

    m = BananaProDef()
    req = _make_request(image_size="2K")
    banana_pro_cost = m.estimate_cost(req)
    gpt_image2_cost = estimate_gpt_image2_points(None, None, "2K")
    # 二者计费逻辑已独立,值应不同
    assert banana_pro_cost != gpt_image2_cost


def test_banana_pro_estimate_cost_never_below_minimum():
    """任何合法参数组合积分 ≥ 1"""
    m = BananaProDef()
    for sz in ("1K", "2K", "4K", None):
        for img_count in (0, 1, 2, 3):
            imgs = [b"x"] * img_count
            req = _make_request(images=imgs, image_size=sz)
            assert m.estimate_cost(req) >= 1
