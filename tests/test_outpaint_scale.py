"""扩图尺寸规划:原图/目标画布超过 1280 时等比缩小,intended 保持预设尺寸。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from RH_ComfyUI.utils.image_process import (
    OUTPAINT_MAX_SIDE,
    OUTPAINT_WORKFLOW_MIN_PAD,
    bump_zero_outpaint_pads,
    crop_image_to_aspect,
    expand_pads_to_aspect,
    pick_tx_outpaint_ratio,
    plan_outpaint_scale,
    preprocess_for_outpaint,
    scale_and_crop_outpaint,
    source_matches_tx_ratio,
)


def _png(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (30, 140, 220))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_small_canvas_no_scale():
    plan = plan_outpaint_scale(800, 600, top=100, left=50, right=50, bottom=100)
    assert plan["scale"] == 1.0
    assert plan["send_w"] == 800
    assert plan["send_h"] == 600
    assert plan["top"] == 100
    assert plan["left"] == 50
    assert plan["out_w"] == 900
    assert plan["out_h"] == 800
    assert plan["intended_w"] == 900
    assert plan["intended_h"] == 800


def test_large_target_scales_to_max_side():
    # 2000x1500 + 200 each side → 2400x1900, scale = 1280/2400
    plan = plan_outpaint_scale(2000, 1500, 200, 200, 200, 200)
    assert plan["scale"] < 1
    assert int(plan["out_w"]) <= OUTPAINT_MAX_SIDE
    assert int(plan["out_h"]) <= OUTPAINT_MAX_SIDE
    assert int(plan["intended_w"]) == 2400
    assert int(plan["intended_h"]) == 1900
    # 等比:输出宽高比 ≈ 目标宽高比
    out_ar = int(plan["out_w"]) / int(plan["out_h"])
    intended_ar = 2400 / 1900
    assert abs(out_ar - intended_ar) < 0.02


def test_huge_source_even_with_zero_pad_would_scale_if_padded():
    plan = plan_outpaint_scale(3000, 2000, 0, 500, 500, 0)
    assert plan["intended_w"] == 4000
    assert plan["intended_h"] == 2000
    assert int(plan["out_w"]) <= OUTPAINT_MAX_SIDE
    assert int(plan["out_h"]) <= OUTPAINT_MAX_SIDE


def test_nonzero_pad_survives_downscale():
    plan = plan_outpaint_scale(4000, 4000, 200, 0, 0, 0)
    assert int(plan["top"]) >= 1
    assert int(plan["left"]) == 0
    assert int(plan["out_w"]) <= OUTPAINT_MAX_SIDE
    assert int(plan["out_h"]) <= OUTPAINT_MAX_SIDE


def test_bump_zero_pads_lifts_only_zeros():
    send, crop = bump_zero_outpaint_pads(0, 520, 520, 0)
    assert send == (OUTPAINT_WORKFLOW_MIN_PAD, 520, 520, OUTPAINT_WORKFLOW_MIN_PAD)
    assert crop == (OUTPAINT_WORKFLOW_MIN_PAD, 0, 0, OUTPAINT_WORKFLOW_MIN_PAD)


def test_bump_zero_pads_keeps_nonzero():
    send, crop = bump_zero_outpaint_pads(80, 40, 40, 80)
    assert send == (80, 40, 40, 80)
    assert crop == (0, 0, 0, 0)


def test_workflow_send_never_zero_after_scale():
    send, _crop = bump_zero_outpaint_pads(0, 500, 500, 0)
    plan = plan_outpaint_scale(3000, 2000, *send)
    assert int(plan["top"]) >= 1
    assert int(plan["left"]) >= 1
    assert int(plan["right"]) >= 1
    assert int(plan["bottom"]) >= 1
    assert int(plan["out_w"]) <= OUTPAINT_MAX_SIDE
    assert int(plan["out_h"]) <= OUTPAINT_MAX_SIDE


def test_expand_pads_restores_16x9_after_lift():
    # 496x864 → 16:9 用户 pad 520/520/0/0,抬 100 后须再加左右,画布回到 16:9
    send, _crop = bump_zero_outpaint_pads(0, 520, 520, 0)
    t, l, r, b = expand_pads_to_aspect(496, 864, *send, 16 / 9)
    assert t == 100 and b == 100
    assert l > 520 and r > 520
    out_w = 496 + l + r
    out_h = 864 + t + b
    assert abs(out_w / out_h - 16 / 9) < 0.02
    assert l == 698 or abs(l - 698) <= 2
    assert r == 698 or abs(r - 698) <= 2
    assert t == 100 and b == 100


def test_max_16x9_from_1600x1400_then_scale():
    # 1600x1400 最大 16:9 = 1600x900(上下各切 250),再缩到 1536x864
    raw = _png(1600, 1400)
    out, box = scale_and_crop_outpaint(
        raw,
        1536,
        864,
        user_top=0,
        user_left=433,
        user_right=433,
        user_bottom=0,
    )
    img = Image.open(BytesIO(out))
    img.load()
    assert box[0] == 0
    assert abs(box[1] - 250) <= 1
    assert box[2] == 1600
    assert abs(box[3] - 900) <= 1
    assert img.size == (1536, 864)


def test_crop_image_to_aspect_max_ratio():
    raw = _png(1600, 1400)
    out, box = crop_image_to_aspect(
        raw,
        1536,
        864,
        user_top=0,
        user_left=520,
        user_right=520,
        user_bottom=0,
    )
    img = Image.open(BytesIO(out))
    img.load()
    assert img.size == (1536, 864)
    assert abs(box[3] - 900) <= 1


def test_preprocess_resizes_bytes():
    raw = _png(2000, 1500)
    out, plan = preprocess_for_outpaint(raw, 200, 200, 200, 200)
    img = Image.open(BytesIO(out))
    img.load()
    assert img.size == (int(plan["send_w"]), int(plan["send_h"]))
    assert img.size[0] < 2000
    assert int(plan["out_w"]) <= OUTPAINT_MAX_SIDE


def test_tx_ratio_rejects_same_as_source():
    assert source_matches_tx_ratio(1920, 1080, "16:9")
    assert not source_matches_tx_ratio(496, 864, "16:9")
    assert pick_tx_outpaint_ratio(1920, 1080, "16:9") is None
    assert pick_tx_outpaint_ratio(496, 864, "16:9") == "16:9"


def test_tx_ratio_infers_from_intended():
    # 496x864 + 16:9 intended 1536x864
    assert pick_tx_outpaint_ratio(496, 864, None, 1536, 864) == "16:9"
    # 保持原比例 → 无法选
    assert pick_tx_outpaint_ratio(496, 864, None, 744, 1296) is None
