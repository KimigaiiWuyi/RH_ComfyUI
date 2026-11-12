"""MiniMax H3-Image:模型注册 / schema / 校验 / 双通道路由 / 组包 / 计费 / 启用闸"""

import asyncio
from typing import Any, Optional

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.types import MediaRef, MediaKind, ContentItem, text_item, image_item
from RH_ComfyUI.models.image.defs import ALL_MODELS, MiniMaxH3ImageDef
from RH_ComfyUI.models.video.defs import MiniMaxH3Def
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.fal.catalog import APP_PREFIXES, FAL_MODEL_NAMES, FAL_SHARED_MODELS
from RH_ComfyUI.utils.backends.fal.payload import prepare
from RH_ComfyUI.utils.backends.minimax.config import (
    MINIMAX_MODEL_OPTIONS,
    MINIMAX_MODEL_H3_IMAGE,
    is_minimax_model_enabled,
)
from RH_ComfyUI.utils.mappers.minimax_h3_image_billing import (
    settle_minimax_h3_image_points,
    estimate_minimax_h3_image_points,
)
from RH_ComfyUI.utils.backends.minimax.h3_image_channel import MiniMaxH3ImageChannel
from RH_ComfyUI.utils.backends.minimax.h3_image_provider import (
    MiniMaxH3ImageProvider,
    MiniMaxH3ImageProviderError,
)


def _t2i(
    *,
    prompt: str = "一只柴犬在樱花树下",
    images: Optional[list[bytes]] = None,
    video_refs: Optional[list[MediaRef]] = None,
    audio_refs: Optional[list[MediaRef]] = None,
    ordered_content: Optional[list[ContentItem]] = None,
    ratio: Optional[str] = None,
    watermark: bool = False,
    seed: Optional[int] = None,
    params: Optional[dict[str, Any]] = None,
) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt=prompt,
        images=images or [],
        video_refs=video_refs or [],
        audio_refs=audio_refs or [],
        ordered_content=ordered_content or [],
        ratio=ratio,
        watermark=watermark,
        seed=seed,
        params=params or {},
    )


def _oc_image(url: str = "https://ex.com/a.png"):
    return image_item(MediaRef(kind=MediaKind.IMAGE, url=url))


# ── 身份 / schema ────────────────────────────────────────────


def test_model_registered_and_distinct_from_video():
    names = [cls.node_def().name for cls in ALL_MODELS]
    assert "minimax_h3_image" in names
    m = MiniMaxH3ImageDef()
    assert m.name == "minimax_h3_image"
    assert m.name != MiniMaxH3Def.node_def().name
    assert m.node.task_type == TaskType("image")


def test_input_schema():
    m = MiniMaxH3ImageDef()
    schema = m.input_schema()
    assert schema["prompt"].required is True
    assert schema["images"].max_items == 16
    assert schema["images"].min_items == 0
    assert "auto" in (schema["ratio"].values or [])
    assert schema["image_size"].values == ["1K", "2K", "4K"]
    assert schema["model_variant"].values == ["standard", "highspeed"]
    assert schema["output_format"].values == ["jpeg", "png", "webp"]
    assert schema["output_compression"].minimum == 0
    assert schema["output_compression"].maximum == 100
    assert schema["watermark"].default is False
    assert schema["prompt_enhanced"].default is True
    assert schema["size"].type.value == "text" or schema["size"].type == "text"
    assert m.supports_edit is True
    assert m.max_input_images == 16


# ── 通道:模型×供应商正交 ──────────────────────────────────────


def test_channel_bindings_official_and_fal():
    m = MiniMaxH3ImageDef()
    bindings = m.channel_bindings()
    names = {b.channel.name for b in bindings}
    assert "minimax-h3-image" in names
    assert "fal" in names
    vendors = {b.channel.name: b.vendor_model for b in bindings}
    assert vendors["minimax-h3-image"] == "MiniMax-H3-Image"
    assert vendors["fal"] == "minimax/h3-image"


def test_fal_catalog_registered():
    assert "minimax_h3_image" in FAL_SHARED_MODELS
    assert "minimax_h3_image" in FAL_MODEL_NAMES
    assert APP_PREFIXES["minimax_h3_image"] == "minimax/h3-image"


def test_minimax_enable_list_gates_official_channel(monkeypatch):
    from RH_ComfyUI.utils.backends.minimax import config as cfg

    assert MINIMAX_MODEL_H3_IMAGE == "minimax_h3_image"
    assert MINIMAX_MODEL_H3_IMAGE in MINIMAX_MODEL_OPTIONS
    monkeypatch.setattr(cfg, "_cfg", lambda key: ["minimax_h3_image"] if key == "MiniMax_Enabled_Models" else None)
    assert is_minimax_model_enabled("minimax_h3_image") is True
    assert is_minimax_model_enabled("minimax_h3") is False
    ch = MiniMaxH3ImageChannel()
    monkeypatch.setattr("RH_ComfyUI.utils.backends.minimax.h3_image_channel.minimax_api_key", lambda: "sk-x")
    monkeypatch.setattr(
        "RH_ComfyUI.utils.backends.minimax.h3_image_channel.is_minimax_model_enabled",
        lambda name: name == "minimax_h3_image",
    )
    assert asyncio.run(ch.check_available()) is True


def test_channel_unavailable_without_key(monkeypatch):
    from RH_ComfyUI.utils.backends.minimax import h3_image_channel as ch_mod

    monkeypatch.setattr(ch_mod, "minimax_api_key", lambda: "")
    monkeypatch.setattr(ch_mod, "is_minimax_model_enabled", lambda name: True)
    ch = MiniMaxH3ImageChannel()
    assert asyncio.run(ch.check_available()) is False
    assert "API Key" in asyncio.run(ch.unavailable_reason())


def test_channel_supports_request():
    ch = MiniMaxH3ImageChannel()
    assert ch.supports_request(_t2i()) is True
    assert ch.supports_request(_t2i(video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")])) is False
    assert ch.supports_request(_t2i(images=[b"x"] * 17)) is False


# ── 模型级 validate ──────────────────────────────────────────


def test_validate_text_to_image_and_edit():
    m = MiniMaxH3ImageDef()
    m.validate(_t2i())
    m.validate(_t2i(images=[b"img"]))
    m.validate(_t2i(images=[b"img"] * 16))
    m.validate(_t2i(ordered_content=[text_item("补充"), _oc_image(), _oc_image("https://ex.com/b.png")]))


def test_validate_rejects_over_16_images():
    m = MiniMaxH3ImageDef()
    with pytest.raises(ValidationError, match="16"):
        m.validate(_t2i(images=[b"x"] * 17))


def test_validate_rejects_non_image_media():
    m = MiniMaxH3ImageDef()
    with pytest.raises(ValidationError):
        m.validate(_t2i(video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")]))
    with pytest.raises(ValidationError):
        m.validate(_t2i(audio_refs=[MediaRef(kind=MediaKind.AUDIO, url="https://ex.com/a.mp3")]))
    oc = [image_item(MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4"))]
    with pytest.raises(ValidationError):
        m.validate(_t2i(ordered_content=oc))


def test_validate_edit_rejects_prompt_enhanced_false():
    m = MiniMaxH3ImageDef()
    with pytest.raises(ValidationError, match="增强"):
        m.validate(_t2i(images=[b"x"], params={"prompt_enhanced": False}))
    # 文生图关增强合法(官方 disable_prompt_enhanced)
    m.validate(_t2i(params={"prompt_enhanced": False}))


def test_validate_rejects_n_over_one():
    m = MiniMaxH3ImageDef()
    with pytest.raises(ValidationError, match="n="):
        m.validate(_t2i(params={"n": 4}))


def test_validate_empty_prompt_rejected():
    m = MiniMaxH3ImageDef()
    with pytest.raises(ValidationError):
        m.validate(_t2i(prompt="  "))


# ── 计费 ─────────────────────────────────────────────────────


def test_estimate_cost_text_and_edit():
    m = MiniMaxH3ImageDef()
    req = _t2i(params={"image_size": "2K"})
    assert m.estimate_cost(req) == estimate_minimax_h3_image_points("2K", 0, variant="standard")
    edit = _t2i(images=[b"a", b"b"], params={"image_size": "4K"})
    assert m.estimate_cost(edit) == estimate_minimax_h3_image_points("4K", 2, variant="standard")
    fast = _t2i(params={"image_size": "1K", "model_variant": "highspeed"})
    assert m.estimate_cost(fast) > m.estimate_cost(_t2i(params={"image_size": "1K"}))


def test_estimate_cost_explicit_size_and_oc_images():
    m = MiniMaxH3ImageDef()
    req = _t2i(params={"size": "3840x2160"})
    assert m.estimate_cost(req) == estimate_minimax_h3_image_points(
        None, 0, variant="standard", width=3840, height=2160
    )
    oc = _t2i(ordered_content=[_oc_image(), _oc_image("https://ex.com/b.png")], params={"image_size": "2K"})
    assert m.estimate_cost(oc) == estimate_minimax_h3_image_points("2K", 2, variant="standard")


def test_point_range():
    lo, hi = MiniMaxH3ImageDef().point_range()
    assert 0 < lo < hi
    assert lo == estimate_minimax_h3_image_points("1K", 0, variant="standard")
    assert hi == estimate_minimax_h3_image_points("4K", 16, variant="highspeed")


def test_settle_cost_official_usage():
    usage = {
        "vendor_usage": {
            "input_tokens_details": {"text_tokens": 3500, "image_tokens": 6000},
            "output_tokens_details": {"image_tokens": 4080},
        },
        "model": "MiniMax-H3-Image",
    }
    got = settle_minimax_h3_image_points(usage)
    assert isinstance(got, int) and got > 0
    assert settle_minimax_h3_image_points({"model": "x"}) is None
    assert settle_minimax_h3_image_points("junk") is None


def test_settle_cost_flat_usage():
    got = settle_minimax_h3_image_points({"text_tokens": 4000, "image_input_tokens": 3000, "image_output_tokens": 4080})
    assert isinstance(got, int) and got > 0


# ── 官方 Provider 组包 / 解析 ─────────────────────────────────


def test_official_render_t2i_body():
    async def _run():
        p = MiniMaxH3ImageProvider(api_key="k")
        method, url, headers, body = await p.render_body(_t2i(params={"image_size": "4K"}), model="MiniMax-H3-Image")
        assert method == "POST"
        assert url.endswith("/v1/images/generations")
        assert headers["Authorization"] == "Bearer k"
        assert body["model"] == "MiniMax-H3-Image"
        assert body["aspect_ratio"] == "auto"
        assert body["area"] == "4K"
        assert "images" not in body
        assert body["response_format"] == "url"

    asyncio.run(_run())


def test_official_render_edit_body_highspeed():
    async def _run():
        p = MiniMaxH3ImageProvider(api_key="k")
        req = _t2i(
            ordered_content=[_oc_image(), _oc_image("https://ex.com/b.png")],
            params={"model_variant": "highspeed", "output_format": "png"},
        )
        _m, url, _h, body = await p.render_body(req)
        assert url.endswith("/v1/images/edits")
        assert body["model"] == "MiniMax-H3-Image-Highspeed"
        assert body["images"] == [
            {"image_url": "https://ex.com/a.png"},
            {"image_url": "https://ex.com/b.png"},
        ]
        assert body["output_format"] == "png"

    asyncio.run(_run())


def test_official_explicit_size_and_watermark():
    async def _run():
        p = MiniMaxH3ImageProvider(api_key="k")
        _m, _u, _h, body = await p.render_body(
            _t2i(watermark=True, params={"size": "2048x2048", "output_compression": 80})
        )
        assert body["size"] == "2048x2048"
        assert "area" not in body and "aspect_ratio" not in body
        assert body["aigc_watermark"] is True
        assert body["output_compression"] == 80

    asyncio.run(_run())


def test_official_parse_response_url_and_error():
    p = MiniMaxH3ImageProvider(api_key="k")
    url, mime, usage, req_id = p.parse_response(
        {
            "data": [{"url": "https://cdn.ex.com/out.png", "output_format": "png"}],
            "usage": {"total_tokens": 10},
            "request_id": "r-1",
        },
        "MiniMax-H3-Image",
    )
    assert url == "https://cdn.ex.com/out.png"
    assert mime == "image/png"
    assert usage["total_tokens"] == 10
    assert req_id == "r-1"

    with pytest.raises(MiniMaxH3ImageProviderError):
        p.parse_response({"data": [{"error": {"code": "E", "message": "boom"}}]}, "m")
    with pytest.raises(MiniMaxH3ImageProviderError):
        p.parse_response({"data": []}, "m")


# ── fal 组包 ─────────────────────────────────────────────────


def test_fal_prepare_text_to_image():
    req = _t2i(ratio="16:9", params={"image_size": "2K"})
    sub = prepare("minimax_h3_image", req)
    assert sub.path == "/minimax/h3-image/text-to-image"
    assert sub.app == "minimax/h3-image"
    assert sub.output == "image"
    assert sub.mode == "text_to_image"
    dims = sub.body["image_size"]
    assert isinstance(dims, dict)
    w, h = dims["width"], dims["height"]
    assert isinstance(w, int) and isinstance(h, int)
    assert w / h == pytest.approx(16 / 9, abs=0.02)
    assert sub.body["num_images"] == 1
    assert sub.body["enable_prompt_expansion"] is True
    assert "image_urls" not in sub.body


def test_fal_prepare_edit_with_oc_urls():
    req = _t2i(
        prompt="把[@参考图片1]的猫换成[@参考图片2]的狗",
        ordered_content=[_oc_image(), _oc_image("https://ex.com/b.png")],
    )
    sub = prepare("minimax_h3_image", req)
    assert sub.path == "/minimax/h3-image/edit"
    assert sub.mode == "edit"
    assert sub.body["image_urls"] == ["https://ex.com/a.png", "https://ex.com/b.png"]
    # ratio 未给 → edit 走 auto(跟随图 1)
    assert sub.body["image_size"] == "auto"
    assert "Image 1" in str(sub.body["prompt"]) and "Image 2" in str(sub.body["prompt"])
    assert "enable_prompt_expansion" not in sub.body


def test_fal_t2i_rejects_auto_ratio():
    with pytest.raises(ValidationError, match="auto"):
        prepare("minimax_h3_image", _t2i(ratio="auto"))


def test_fal_rejects_watermark_seed_highspeed():
    with pytest.raises(ValidationError, match="水印"):
        prepare("minimax_h3_image", _t2i(ratio="16:9", watermark=True))
    with pytest.raises(ValidationError, match="seed"):
        prepare("minimax_h3_image", _t2i(ratio="16:9", seed=42))
    with pytest.raises(ValidationError, match="highspeed"):
        prepare("minimax_h3_image", _t2i(ratio="16:9", params={"model_variant": "highspeed"}))


def test_fal_explicit_size_and_bounds():
    sub = prepare("minimax_h3_image", _t2i(params={"size": "2048x2048"}))
    assert sub.body["image_size"] == {"width": 2048, "height": 2048}
    with pytest.raises(ValidationError):
        prepare("minimax_h3_image", _t2i(params={"size": "100x100"}))
    with pytest.raises(ValidationError):
        prepare("minimax_h3_image", _t2i(params={"size": "4000x1000"}))


def test_fal_rejects_non_image_media_and_n():
    with pytest.raises(ValidationError):
        prepare(
            "minimax_h3_image",
            _t2i(ratio="16:9", video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://ex.com/v.mp4")]),
        )
    with pytest.raises(ValidationError, match="n="):
        prepare("minimax_h3_image", _t2i(ratio="16:9", params={"n": 2}))


def test_fal_dims_tiers_within_bounds():
    for ratio in ("1:1", "21:9", "9:16", "3:1"):
        for tier in ("1K", "2K", "4K"):
            sub = prepare(
                "minimax_h3_image",
                _t2i(ratio=ratio, params={"image_size": tier}),
            )
            dims = sub.body["image_size"]
            assert isinstance(dims, dict)
            w, h = dims["width"], dims["height"]
            assert isinstance(w, int) and isinstance(h, int)
            assert w % 16 == 0 and h % 16 == 0
            assert 1 / 3 <= w / h <= 3
            assert w <= 7745 and h <= 7745


def test_fal_t2i_default_ratio_adaptive_square():
    # 当调用方未传 ratio 时，fal 文生图自动自适应兜底为 1:1 方图，不抛出异常
    sub = prepare("minimax_h3_image", _t2i())
    dims = sub.body["image_size"]
    assert isinstance(dims, dict)
    assert dims["width"] == dims["height"]


def test_extract_aspect_ratio_and_size():
    from RH_ComfyUI.utils.core.parser import extract_aspect_ratio_and_size

    # 1. 自然语言中带有 16:9 与横版描述
    p1 = "日系动漫赛璐珞质感的黄昏海边小镇风景，横版构图，16:9 宽幅，画质清晰。"
    cleaned1, r1, s1 = extract_aspect_ratio_and_size(p1)
    assert r1 == "16:9"

    # 2. 显式命令行 --ar 16:9 提取并剥离
    p2 = "--ar 16:9 一只可爱的小猫咪在玩耍"
    cleaned2, r2, s2 = extract_aspect_ratio_and_size(p2)
    assert r2 == "16:9"
    assert "--ar" not in cleaned2
    assert "一只可爱的小猫咪在玩耍" in cleaned2

    # 3. 自然语言语义词：立绘 -> 9:16
    p3 = "少女全身立绘，二次元风格"
    cleaned3, r3, s3 = extract_aspect_ratio_and_size(p3)
    assert r3 == "9:16"

    # 4. 自然语言语义词：头像 -> 1:1
    p4 = "可爱小狗头像"
    cleaned4, r4, s4 = extract_aspect_ratio_and_size(p4)
    assert r4 == "1:1"

    # 5. 显式尺寸 --size 1920x1080 提取并剥离，推导比例为 16:9
    p5 = "--size 1920x1080 浩瀚星空"
    cleaned5, r5, s5 = extract_aspect_ratio_and_size(p5)
    assert s5 == (1920, 1080)
    assert r5 == "16:9"
    assert "--size" not in cleaned5

    # 6. 无任何画幅要求时返回 None
    p6 = "纯粹的一句话描述"
    cleaned6, r6, s6 = extract_aspect_ratio_and_size(p6)
    assert r6 is None
    assert s6 is None
    assert cleaned6 == p6
