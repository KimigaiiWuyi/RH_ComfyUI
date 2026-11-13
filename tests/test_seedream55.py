"""Seedream 5.5(doubao-seedream-5-5)— mapper / 计费 / 跨字段校验 / 注册

覆盖:
- seedream55_mapper:size 档位/显式像素/auto、ratio 进 prompt、参考图
  单 str / 多 list / data URL 回落、background、layer_decomposition
  + options(max_images / text_recognition)、safety_identifier、
  optimize_mode 白名单、图层拆分多图响应 → outputs/layer_* + usage.layers
- seedream55_billing:输入图 5 积分/张(首张起计)、输出 13/23 积分/张
  按像素分档、拆分按 max_images 预扣、settle 按 data[] 实算
- Seedream55ImageModel.validate:prompt/图数/size/background 组合约束
- 注册:seedream5_5 进 catalog + ARK 启用列表
"""

from __future__ import annotations

import asyncio

import pytest
from PIL import Image

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.models.image.defs import Seedream55Def
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.mappers.seedream import seedream55_mapper
from RH_ComfyUI.utils.backends.seedream.api import SeedreamAPI
from RH_ComfyUI.utils.mappers.seedream55_billing import (
    OUTPUT_COST_LOW_POINTS,
    INPUT_IMAGE_COST_POINTS,
    OUTPUT_COST_HIGH_POINTS,
    settle_seedream55_points,
    estimate_seedream55_points,
)

MODEL_ID = "doubao-seedream-5-5-261028"

# ── 工具 ──


def _make_request(
    *,
    prompt: str = "test",
    ratio: str | None = None,
    images: list[bytes] | None = None,
    model: str = MODEL_ID,
    **params: object,
) -> GenerationRequest:
    p: dict = {"model": model}
    p.update(params)
    return GenerationRequest(
        task_type=TaskType("image"),
        prompt=prompt,
        ratio=ratio,
        width=0,
        height=0,
        params=p,
        images=images or [],
    )


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _FakeSeedreamAPI(SeedreamAPI):
    """SeedreamAPI.generate 的显式 stub:记录 body 与 decode_all,返回新多图结构。"""

    def __init__(self, items: list[dict] | None = None) -> None:
        self.items = (
            items
            if items is not None
            else [{"url": "https://r2.example.com/out.png", "size": "2048x2048", "z_index": 0}]
        )
        self.body: dict = {}
        self.decode_all = False

    async def generate(self, body: dict, *, decode_all: bool = False) -> dict:
        self.body = body
        self.decode_all = decode_all
        items = self.items
        images = [Image.new("RGB", (64, 64), (i * 40 % 256, 128, 128)) for i in range(len(items))]
        first = items[0]
        return {
            "image": images[0],
            "images": images,
            "items": items,
            "model": body["model"] if "model" in body else "",
            "size": first["size"] if "size" in first else "",
            "output_format": "png",
            "generated_images": len(items),
            "input_images": 0,
            "raw": {"data": items, "usage": {"generated_images": len(items), "input_images": 0}},
        }


def _run_mapper(req: GenerationRequest, items: list[dict] | None = None):
    api = _FakeSeedreamAPI(items)
    out = asyncio.run(seedream55_mapper(req, api))
    return out, api.body


# ═══════════════════════════════════════════════════════════════════════
#  mapper:decode_all / 文生图 / size 解析
# ═══════════════════════════════════════════════════════════════════════


def test_mapper_requests_full_decode():
    """5.5 走 decode_all=True(图层拆分要多图;5.0 保持首图语义)。"""
    api = _FakeSeedreamAPI()
    asyncio.run(seedream55_mapper(_make_request(images=[_PNG], layer_decomposition=True), api))
    assert api.decode_all is True


# ═══════════════════════════════════════════════════════════════════════
#  mapper:文生图 / size 解析
# ═══════════════════════════════════════════════════════════════════════


def test_mapper_t2i_flat_body():
    """文生图:flat body,默认档位 2K,无 image 字段,无废弃字段。"""
    _, body = _run_mapper(_make_request(prompt="一只柴犬", image_size="2K"))
    assert body["model"] == MODEL_ID
    assert body["prompt"] == "一只柴犬"
    assert body["size"] == "2K"
    assert body["response_format"] == "url"
    assert body["output_format"] == "png"
    assert body["watermark"] is False
    assert "image" not in body
    assert "sequential_image_generation" not in body
    assert "tools" not in body
    assert "stream" not in body


def test_mapper_ratio_hint_appended_in_tier_mode():
    """档位模式:ratio 按官方推荐以自然语言拼进 prompt。"""
    _, body = _run_mapper(_make_request(prompt="雪山", ratio="16:9", image_size="1K"))
    assert "画面宽高比 16:9" in body["prompt"]
    assert body["size"] == "1K"


def test_mapper_explicit_size_skips_ratio_hint():
    """显式 WxH:ratio 不拼进 prompt,size 原样透传。"""
    _, body = _run_mapper(_make_request(prompt="雪山", ratio="16:9", size="2048x1152"))
    assert body["size"] == "2048x1152"
    assert body["prompt"] == "雪山"


def test_mapper_size_field_accepts_tier():
    """size 字段直接给档位串也合法(与上游 size 语义一致)。"""
    _, body = _run_mapper(_make_request(size="1.5K"))
    assert body["size"] == "1.5K"


def test_mapper_size_auto_ignored_in_generation():
    """图片生成传 size=auto → 不落进 body(回落档位)。"""
    _, body = _run_mapper(_make_request(size="auto", image_size="1K"))
    assert body["size"] == "1K"


def test_mapper_width_height_params():
    """width/height 参数对 → 'WxH'。"""
    _, body = _run_mapper(_make_request(width=2048, height=1152))
    assert body["size"] == "2048x1152"


def test_mapper_size_mode_compat_key():
    """兼容旧键 size_mode(通用 estimate API 用 image_size,两者都读)。"""
    _, body = _run_mapper(_make_request(size_mode="1.5K"))
    assert body["size"] == "1.5K"


def test_mapper_response_format_watermark():
    """response_format=b64_json + watermark=True 透传。"""
    _, body = _run_mapper(_make_request(response_format="b64_json", watermark=True))
    assert body["response_format"] == "b64_json"
    assert body["watermark"] is True


def test_mapper_seed_passthrough():
    """显式 seed 透传。"""
    req = _make_request()
    req.seed = 42
    _, body = _run_mapper(req)
    assert body["seed"] == 42


def test_mapper_safety_identifier():
    """safety_identifier 透传。"""
    _, body = _run_mapper(_make_request(safety_identifier="user-abc"))
    assert body["safety_identifier"] == "user-abc"


def test_mapper_background_gen_only():
    """background 只在图片生成模式透传。"""
    _, body = _run_mapper(_make_request(background="transparent"))
    assert body["background"] == "transparent"


def test_mapper_optimize_standard_ok():
    """optimize_mode=standard → optimize_prompt_options。"""
    _, body = _run_mapper(_make_request(optimize_mode="standard"))
    assert body["optimize_prompt_options"] == {"mode": "standard"}


def test_mapper_optimize_fast_rejected():
    """5.5 不支持 fast 提示词优化 → RuntimeError。"""
    with pytest.raises(RuntimeError, match="standard"):
        _run_mapper(_make_request(optimize_mode="fast"))


def test_mapper_missing_model_raises():
    """未注入 backend_model → RuntimeError。"""
    req = _make_request(model="")
    with pytest.raises(RuntimeError, match="backend_model"):
        _run_mapper(req)


# ═══════════════════════════════════════════════════════════════════════
#  mapper:参考图
# ═══════════════════════════════════════════════════════════════════════


def test_mapper_single_image_url_as_str():
    """_image_urls 单图 → image 为 str(与 ARK 文档一致)。"""
    req = _make_request(images=[_PNG])
    req.params["_image_urls"] = ["https://r2.example.com/ref.png"]
    _, body = _run_mapper(req)
    assert body["image"] == "https://r2.example.com/ref.png"


def test_mapper_multi_image_urls_as_list():
    """_image_urls 多图 → image 为 list[str]。"""
    req = _make_request(images=[_PNG, _PNG])
    req.params["_image_urls"] = [
        "https://r2.example.com/a.png",
        "https://r2.example.com/b.png",
    ]
    _, body = _run_mapper(req)
    assert body["image"] == ["https://r2.example.com/a.png", "https://r2.example.com/b.png"]


def test_mapper_images_fallback_data_url():
    """无外链 → data URL 回落(数组形态)。"""
    _, body = _run_mapper(_make_request(images=[_PNG]))
    assert isinstance(body["image"], list)
    assert body["image"][0].startswith("data:image/png;base64,")


# ═══════════════════════════════════════════════════════════════════════
#  mapper:图层拆分
# ═══════════════════════════════════════════════════════════════════════


def test_mapper_layer_body_defaults():
    """图层拆分:默认 max_images=30,options 不带 text_recognition。"""
    req = _make_request(prompt="", images=[_PNG], layer_decomposition=True)
    _, body = _run_mapper(req)
    assert body["layer_decomposition"] is True
    assert body["layer_decomposition_options"] == {"max_images": 30}
    # prompt 留空 → 不传,由模型自动识别全部主要元素
    assert "prompt" not in body
    # background 不可出现在图层拆分
    assert "background" not in body


def test_mapper_layer_options_max_and_ocr():
    """layer_max_images + text_recognition 进 layer_decomposition_options。"""
    req = _make_request(
        prompt="拆分这张海报",
        images=[_PNG],
        layer_decomposition=True,
        layer_max_images=5,
        text_recognition=True,
    )
    _, body = _run_mapper(req)
    assert body["layer_decomposition_options"] == {"max_images": 5, "text_recognition": True}
    assert body["prompt"] == "拆分这张海报"


def test_mapper_layer_size_auto_and_tier():
    """图层拆分 size 接受 auto / 档位,不接受显式像素。"""
    req = _make_request(images=[_PNG], layer_decomposition=True, size="auto")
    _, body = _run_mapper(req)
    assert body["size"] == "auto"

    req2 = _make_request(images=[_PNG], layer_decomposition=True, image_size="1.5K")
    _, body2 = _run_mapper(req2)
    assert body2["size"] == "1.5K"

    req3 = _make_request(images=[_PNG], layer_decomposition=True, size="2048x2048")
    _, body3 = _run_mapper(req3)
    assert "x" not in body3["size"]  # 显式像素不进 wire


def test_mapper_layer_response_multi_outputs():
    """图层拆分响应:底图(z=0)→ data/outputs['image'],图层 → outputs['layer_{z}']。"""
    items = [
        {
            "url": "https://r2.example.com/base.png",
            "size": "2048x2048",
            "z_index": 0,
            "name": "base",
        },
        {
            "url": "https://r2.example.com/l1.png",
            "size": "512x512",
            "z_index": 1,
            "name": "title",
            "bounding_box": {"absolute": {"x": 10, "y": 20, "width": 100, "height": 40}},
            "text_recognition": {"text": "SALE"},
        },
        {
            "url": "https://r2.example.com/l2.png",
            "size": "256x256",
            "z_index": 2,
            "name": "logo",
        },
    ]
    req = _make_request(images=[_PNG], layer_decomposition=True, text_recognition=True)
    out, _ = _run_mapper(req, items)

    assert out.status == "ok"
    assert out.data == out.outputs["image"]  # 底图是主产物
    assert set(out.outputs) == {"image", "layer_01", "layer_02"}
    layers = out.usage["layers"]
    assert len(layers) == 3
    assert layers[0]["z_index"] == 0
    assert layers[1]["name"] == "title"
    assert layers[1]["bounding_box"]["absolute"]["x"] == 10
    assert layers[1]["text_recognition"]["text"] == "SALE"
    assert out.usage["generated_images"] == 3


def test_mapper_layer_missing_base_raises():
    """响应缺 z_index=0 底图 → RuntimeError。"""
    items = [
        {"url": "https://r2.example.com/l1.png", "z_index": 1},
        {"url": "https://r2.example.com/l2.png", "z_index": 2},
    ]
    req = _make_request(images=[_PNG], layer_decomposition=True)
    with pytest.raises(RuntimeError, match="底图"):
        _run_mapper(req, items)


def test_mapper_layer_non_int_z_index_uses_position():
    """z_index 非整数(字符串/缺失)不再当成 0 覆盖底图,按位置进 layer_*。"""
    items = [
        {"url": "https://r2.example.com/base.png", "z_index": 0},
        {"url": "https://r2.example.com/l1.png", "z_index": "1"},
        {"url": "https://r2.example.com/l2.png"},
    ]
    req = _make_request(images=[_PNG], layer_decomposition=True)
    out, _ = _run_mapper(req, items)
    assert set(out.outputs) == {"image", "layer_01", "layer_02"}


# ═══════════════════════════════════════════════════════════════════════
#  计费:常量与估算
# ═══════════════════════════════════════════════════════════════════════


def test_billing_constants():
    assert INPUT_IMAGE_COST_POINTS == 5
    assert OUTPUT_COST_LOW_POINTS == 13
    assert OUTPUT_COST_HIGH_POINTS == 23


@pytest.mark.parametrize(
    ("n_in", "size", "expected"),
    [
        (0, "1K", 13),  # 0 输入 + 1K(13)
        (0, "1.5K", 13),  # 1.5K ≤ 2.61M → 13
        (0, "2K", 23),  # 2K → 23
        (0, None, 23),  # 缺省按 2K
        (1, "1K", 18),  # 1 输入(5) + 13
        (3, "1K", 28),  # 3 输入(15) + 13(与 5.0 Pro 不同:首张也计)
        (10, "2K", 73),  # 10 输入(50) + 23
        (0, "1024x1024", 13),  # 显式像素 ≤2.61M
        (0, "2048x2048", 23),  # 4.19M → 高档
        (0, "auto", 23),  # auto 按高档估
        (0, "8K", 23),  # 未知档按高档
    ],
)
def test_estimate_seedream55_points(n_in, size, expected):
    assert estimate_seedream55_points(n_in, size) == expected


def test_estimate_layer_mode_max_images():
    """拆分按 layer_max_images 预扣:1 输入 + 30 张 2K。"""
    assert estimate_seedream55_points(1, "2K", layer_decomposition=True, layer_max_images=30) == 5 + 30 * 23
    # max_images 下限 clamp 到 2
    assert estimate_seedream55_points(1, "1K", layer_decomposition=True, layer_max_images=1) == 5 + 2 * 13
    # 缺省 30
    assert estimate_seedream55_points(1, "1K", layer_decomposition=True, layer_max_images=0) == 5 + 30 * 13


# ═══════════════════════════════════════════════════════════════════════
#  计费:settle(上游实扣)
# ═══════════════════════════════════════════════════════════════════════


def _usage_with_raw(data: list[dict], input_images: int = 0, usage_input: int | None = None):
    raw: dict = {"data": data, "usage": {"generated_images": len(data)}}
    if usage_input is not None:
        raw["usage"]["input_images"] = usage_input
    return {"raw_task": raw, "input_images": input_images}


def test_settle_single_image():
    usage = _usage_with_raw([{"size": "1024x1024"}], input_images=1)
    assert settle_seedream55_points(usage) == 5 + 13


def test_settle_high_tier():
    usage = _usage_with_raw([{"size": "2048x2048"}], input_images=2)
    assert settle_seedream55_points(usage) == 10 + 23


def test_settle_layer_mode_per_item_tiers():
    """拆分:逐张按各自像素分档 + 输入图。"""
    usage = _usage_with_raw(
        [
            {"size": "2048x2048"},  # 底图高档 23
            {"size": "512x512"},  # 低档 13
            {"size": "1024x1024"},  # 低档 13
        ],
        input_images=1,
    )
    assert settle_seedream55_points(usage) == 5 + 23 + 13 + 13


def test_settle_input_images_fallback_to_raw_usage():
    """usage.input_images 缺失 → 回退 raw_task.usage.input_images。"""
    usage = {"raw_task": {"data": [{"size": "1024x1024"}], "usage": {"input_images": 2}}}
    assert settle_seedream55_points(usage) == 10 + 13


def test_settle_missing_data_returns_none():
    """无 raw_task / data → None(维持预扣)。"""
    assert settle_seedream55_points({}) is None
    assert settle_seedream55_points({"raw_task": {"data": []}}) is None
    assert settle_seedream55_points("junk") is None


def test_settle_unparseable_size_returns_none():
    """data[].size 解析不出 → None(维持预扣,不猜价)。"""
    usage = _usage_with_raw([{"size": "bogus"}], input_images=1)
    assert settle_seedream55_points(usage) is None


# ═══════════════════════════════════════════════════════════════════════
#  validate:跨字段约束
# ═══════════════════════════════════════════════════════════════════════


def _model() -> Seedream55Def:
    return Seedream55Def()


def test_validate_t2i_ok():
    _model().validate(_make_request(prompt="ok"))


def test_validate_gen_requires_prompt():
    with pytest.raises(ValidationError, match="提示词"):
        _model().validate(_make_request(prompt=""))


def test_validate_layer_prompt_optional():
    """图层拆分允许空 prompt。"""
    _model().validate(_make_request(prompt="", images=[_PNG], layer_decomposition=True))


def test_validate_layer_requires_exactly_one_image():
    m = _model()
    with pytest.raises(ValidationError, match="恰好 1 张"):
        m.validate(_make_request(images=[], layer_decomposition=True))
    with pytest.raises(ValidationError, match="恰好 1 张"):
        m.validate(_make_request(images=[_PNG, _PNG], layer_decomposition=True))


def test_validate_layer_rejects_explicit_size():
    with pytest.raises(ValidationError, match="显式像素"):
        _model().validate(_make_request(images=[_PNG], layer_decomposition=True, size="2048x2048"))


def test_validate_layer_rejects_background():
    with pytest.raises(ValidationError, match="background"):
        _model().validate(_make_request(images=[_PNG], layer_decomposition=True, background="transparent"))
    # auto 背景(端口默认值)允许
    _model().validate(_make_request(images=[_PNG], layer_decomposition=True, background="auto"))


def test_validate_layer_max_images_bounds():
    m = _model()
    with pytest.raises(ValidationError, match="layer_max_images"):
        m.validate(_make_request(images=[_PNG], layer_decomposition=True, layer_max_images=101))
    with pytest.raises(ValidationError, match="layer_max_images"):
        m.validate(_make_request(images=[_PNG], layer_decomposition=True, layer_max_images=1))
    with pytest.raises(ValidationError, match="整数"):
        m.validate(_make_request(images=[_PNG], layer_decomposition=True, layer_max_images="abc"))


def test_validate_gen_rejects_auto_tier():
    with pytest.raises(ValidationError, match="auto"):
        _model().validate(_make_request(image_size="auto"))
    with pytest.raises(ValidationError, match="auto"):
        _model().validate(_make_request(size="auto"))


def test_validate_gen_rejects_bad_tier():
    with pytest.raises(ValidationError, match="image_size"):
        _model().validate(_make_request(image_size="3K"))


def test_validate_gen_size_string_must_be_tier_or_wh():
    with pytest.raises(ValidationError, match="size"):
        _model().validate(_make_request(size="garbage"))
    with pytest.raises(ValidationError, match="字符串"):
        _model().validate(_make_request(size=123))


def test_validate_explicit_size_pixel_bounds():
    with pytest.raises(ValidationError, match="总像素"):
        _model().validate(_make_request(size="100x100"))
    # 8192x256 = 2097152 像素在界内,但宽高比 32 超界
    with pytest.raises(ValidationError, match="宽高比"):
        _model().validate(_make_request(size="8192x256"))
    _model().validate(_make_request(size="2048x2048"))


def test_validate_transparent_requires_png():
    with pytest.raises(ValidationError, match="jpeg"):
        _model().validate(_make_request(background="transparent", output_format="jpeg"))
    _model().validate(_make_request(background="transparent", output_format="png"))


def test_validate_background_enum():
    with pytest.raises(ValidationError, match="background"):
        _model().validate(_make_request(background="blur"))


def test_validate_max_images():
    with pytest.raises(ValidationError, match="10"):
        _model().validate(_make_request(images=[_PNG] * 11))


def test_validate_safety_identifier_len():
    with pytest.raises(ValidationError, match="safety_identifier"):
        _model().validate(_make_request(safety_identifier="x" * 65))
    _model().validate(_make_request(safety_identifier="x" * 64))


def test_validate_optimize_mode_whitelist():
    with pytest.raises(ValidationError, match="standard"):
        _model().validate(_make_request(optimize_mode="fast"))
    _model().validate(_make_request(optimize_mode="standard"))


def test_validate_forbidden_params():
    m = _model()
    for key in ("sequential_image_generation", "sequential_image_generation_options", "tools", "stream"):
        req = _make_request()
        req.params[key] = "auto" if key == "sequential_image_generation" else []
        with pytest.raises(ValidationError, match="不支持"):
            m.validate(req)


# ═══════════════════════════════════════════════════════════════════════
#  estimate_cost / settle_cost / 注册
# ═══════════════════════════════════════════════════════════════════════


def test_model_estimate_cost():
    m = _model()
    assert m.estimate_cost(_make_request(image_size="1K")) == 13
    assert m.estimate_cost(_make_request(image_size="2K", images=[_PNG, _PNG])) == 10 + 23
    assert (
        m.estimate_cost(_make_request(images=[_PNG], layer_decomposition=True, layer_max_images=10, image_size="1K"))
        == 5 + 10 * 13
    )


def test_model_settle_cost():
    m = _model()
    usage = _usage_with_raw([{"size": "1024x1024"}], input_images=1)
    assert m.settle_cost(_make_request(), usage) == 18
    assert m.settle_cost(_make_request(), {"bad": 1}) is None


def test_model_point_range():
    lo, hi = _model().point_range()
    assert lo == 13
    assert hi == 5 + 100 * 23


def test_model_registered_and_enabled_by_ark():
    """seedream5_5 进入内置模型注册表 + ARK 启用列表。"""
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.core.routing.registry import model_registry

    init_backends()
    discover_builtin_models()
    try:
        model = model_registry.get("seedream5_5")
    finally:
        model_registry.clear()
    assert model is not None and model.node is not None
    assert model.node.backend == "seedream"
    assert model.node.backend_model == MODEL_ID

    from RH_ComfyUI.rh_config.service_config import _IMAGE_MODEL_REAL_NAMES, _SEEDANCE_ARK_MODEL_NAMES

    assert "seedream5_5" in _SEEDANCE_ARK_MODEL_NAMES
    assert "seedream5_5" in _IMAGE_MODEL_REAL_NAMES


def test_estimate_model_points_end_to_end():
    """estimate API 口径:num_input_images / image_size 进 params。"""
    from RH_ComfyUI.models import discover_builtin_models
    from RH_ComfyUI.rh_models.api import estimate_model_points
    from RH_ComfyUI.utils.backends import init_backends
    from RH_ComfyUI.core.routing.registry import model_registry

    init_backends()
    discover_builtin_models()
    try:
        r0 = asyncio.run(estimate_model_points("seedream5_5", image_size="1K", num_input_images=0))
        r2 = asyncio.run(estimate_model_points("seedream5_5", image_size="1K", num_input_images=2))
    finally:
        model_registry.clear()
    assert r0["point_cost"] == 13
    assert r2["point_cost"] == 23  # 2 输入(10) + 13
