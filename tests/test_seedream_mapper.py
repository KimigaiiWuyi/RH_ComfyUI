"""Seedream 5.0 (Lite / Pro) mapper — size 映射 + 响应解析

覆盖重点:
- resolve_seedream_size:size_mode(档位) + ratio(宽高比) → 像素值(方式 2)
- Pro 与 Lite 使用不同映射表
- 无效档位 / 无效宽高比时回落默认值
- seedream_mapper:flat body 组装 + 参考图(R2 外链)注入
"""

from __future__ import annotations

import io
import asyncio
from unittest.mock import MagicMock

import pytest
from PIL import Image

from RH_ComfyUI.utils.mappers.seedream import (
    resolve_seedream_size,
    seedream_mapper,
    _is_pro_model,
    _SEEDREAM_PRO_SIZE_MAP,
    _SEEDREAM_LITE_SIZE_MAP,
)
from RH_ComfyUI.utils.core.request import GenerationRequest, TaskType


# ── 工具 ──


def _make_request(
    *,
    prompt: str = "test",
    ratio: str = "1:1",
    size_mode: str | None = None,
    images: list[bytes] | None = None,
    watermark: bool | None = None,
    response_format: str = "url",
    output_format: str = "png",
    model: str = "seedream-5.0",
) -> GenerationRequest:
    """快速构造 GenerationRequest(仅 mapper 需要的字段)。"""
    params: dict = {"model": model}
    if size_mode is not None:
        params["size_mode"] = size_mode
    if watermark is not None:
        params["watermark"] = watermark
    if response_format != "url":
        params["response_format"] = response_format
    if output_format != "png":
        params["output_format"] = output_format
    return GenerationRequest(
        task_type=TaskType("image"),
        prompt=prompt,
        ratio=ratio,
        width=0,
        height=0,
        params=params,
        images=images or [],
    )


def _make_fake_api(url: str = "https://r2.example.com/out.png") -> MagicMock:
    """构造一个 fake SeedreamAPI.generate 返回结构。"""
    async def _fake_generate(body):
        return {
            "image": Image.new("RGB", (64, 64), color=(128, 128, 128)),
            "model": body.get("model", ""),
            "size": body.get("size", ""),
            "output_format": "png",
            "generated_images": 1,
            "raw": {"data": [{"url": url}]},
        }
    api = MagicMock()
    api.generate = _fake_generate
    return api


# ── Pro / Lite 模型判定 ──


@pytest.mark.parametrize(
    ("vendor_model", "want_pro"),
    [
        ("doubao-seedream-5.0-pro", True),
        ("seedream-5.0-pro", True),
        ("seedream-5.0", False),
        ("seedream-5.0-lite", False),
        ("", False),
    ],
)
def test_is_pro_model(vendor_model, want_pro):
    assert _is_pro_model(vendor_model) is want_pro


# ── resolve_seedream_size:Pro 映射表 ──


@pytest.mark.parametrize(
    ("size_mode", "ratio", "expected"),
    [
        ("1K", "1:1", "1024x1024"),
        ("1K", "16:9", "1424x800"),
        ("1K", "9:16", "800x1424"),
        ("2K", "1:1", "2048x2048"),
        ("2K", "16:9", "2816x1584"),
        ("2K", "9:16", "1584x2816"),
        ("2K", "4:3", "2368x1776"),
        ("2K", "3:4", "1776x2368"),
        ("2K", "3:2", "2496x1664"),
        ("2K", "2:3", "1664x2496"),
        ("2K", "21:9", "3136x1344"),
    ],
)
def test_resolve_seedream_size_pro(size_mode, ratio, expected):
    assert resolve_seedream_size("doubao-seedream-5.0-pro", size_mode, ratio) == expected


# ── resolve_seedream_size:Lite 映射表 ──


@pytest.mark.parametrize(
    ("size_mode", "ratio", "expected"),
    [
        ("2K", "1:1", "2048x2048"),
        ("2K", "16:9", "2848x1600"),
        ("3K", "1:1", "3072x3072"),
        ("3K", "16:9", "4096x2304"),
        ("4K", "1:1", "4096x4096"),
        ("4K", "16:9", "5504x3040"),
        ("4K", "9:16", "3040x5504"),
        ("4K", "21:9", "6240x2656"),
    ],
)
def test_resolve_seedream_size_lite(size_mode, ratio, expected):
    assert resolve_seedream_size("seedream-5.0", size_mode, ratio) == expected


# ── resolve_seedream_size:缺省 / 回落 ──


def test_resolve_seedream_size_default_size_mode():
    """size_mode 为空 → 默认 2K。"""
    # Pro 2K 1:1
    assert resolve_seedream_size("doubao-seedream-5.0-pro", None, "1:1") == "2048x2048"
    # Lite 2K 1:1
    assert resolve_seedream_size("seedream-5.0", None, "1:1") == "2048x2048"


def test_resolve_seedream_size_default_ratio():
    """ratio 为空 → 默认 1:1。"""
    assert resolve_seedream_size("seedream-5.0", "4K", None) == "4096x4096"


def test_resolve_seedream_size_invalid_tier_falls_back_to_2k():
    """Pro 传了 4K(不存在) → 回落 2K。"""
    # Pro 不支持 4K,回落 2K
    result = resolve_seedream_size("doubao-seedream-5.0-pro", "4K", "1:1")
    assert result == "2048x2048"  # Pro 2K 1:1


def test_resolve_seedream_size_invalid_ratio_falls_back_to_2048():
    """不存在的宽高比 → 回落 2048x2048。"""
    result = resolve_seedream_size("seedream-5.0", "2K", "5:4")
    assert result == "2048x2048"


# ── 映射表完整性:Pro 仅 1K/2K / Lite 仅 2K/3K/4K ──


def test_pro_size_map_has_only_1k_2k():
    """Pro 只支持 1K/2K 两档。"""
    assert set(_SEEDREAM_PRO_SIZE_MAP.keys()) == {"1K", "2K"}


def test_lite_size_map_has_only_2k_3k_4k():
    """Lite 只支持 2K/3K/4K 三档。"""
    assert set(_SEEDREAM_LITE_SIZE_MAP.keys()) == {"2K", "3K", "4K"}


def test_size_map_all_ratios_present():
    """每个档位都覆盖全部 8 种宽高比。"""
    expected_ratios = {"1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3", "21:9"}
    for tier_map in (*_SEEDREAM_PRO_SIZE_MAP.values(), *_SEEDREAM_LITE_SIZE_MAP.values()):
        assert set(tier_map.keys()) == expected_ratios


# ── seedream_mapper:flat body 组装 ──


def test_seedream_mapper_builds_flat_body():
    """mapper 输出 flat body(顶层 prompt / size / model,无 envelope)。"""
    api = _make_fake_api()
    req = _make_request(
        prompt="星际穿越",
        ratio="16:9",
        size_mode="2K",
        model="doubao-seedream-5.0-pro",
    )

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))

    body = captured["body"]
    # flat 形态:顶层字段
    assert body["model"] == "doubao-seedream-5.0-pro"
    assert body["prompt"] == "星际穿越"
    assert body["size"] == "2816x1584"  # Pro 2K 16:9
    assert body["watermark"] is False
    assert body["response_format"] == "url"
    # 无 envelope 字段
    assert "input" not in body
    assert "parameters" not in body


def test_seedream_mapper_default_watermark_false():
    """没传 watermark → 默认 False。"""
    api = _make_fake_api()
    req = _make_request(model="seedream-5.0")

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))
    assert captured["body"]["watermark"] is False


def test_seedream_mapper_explicit_watermark():
    """显式 watermark=True → 透传。"""
    api = _make_fake_api()
    req = _make_request(model="seedream-5.0", watermark=True)

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))
    assert captured["body"]["watermark"] is True


def test_seedream_mapper_size_mode_4k_lite():
    """Lite 4K + 16:9 → 5504x3040。"""
    api = _make_fake_api()
    req = _make_request(ratio="16:9", size_mode="4K", model="seedream-5.0")

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))
    assert captured["body"]["size"] == "5504x3040"


# ── seedream_mapper:参考图(R2 外链)注入 ──


def test_seedream_mapper_injects_r2_image_urls():
    """request.params['_image_urls'] 存在时,注入 image 字段(单图 str / 多图 list)。"""
    api = _make_fake_api()
    # 单图
    req = _make_request(
        model="seedream-5.0",
        images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 16],
    )
    req.params["_image_urls"] = ["https://r2.example.com/ref.png"]

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))
    # 单图传 str
    assert captured["body"]["image"] == "https://r2.example.com/ref.png"


def test_seedream_mapper_multi_images_as_list():
    """多张参考图 → image 字段为 list[str]。"""
    api = _make_fake_api()
    req = _make_request(
        model="seedream-5.0",
        images=[b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, b"\x89PNG\r\n\x1a\n" + b"\x11" * 16],
    )
    req.params["_image_urls"] = [
        "https://r2.example.com/ref1.png",
        "https://r2.example.com/ref2.png",
    ]

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))
    assert isinstance(captured["body"]["image"], list)
    assert captured["body"]["image"] == [
        "https://r2.example.com/ref1.png",
        "https://r2.example.com/ref2.png",
    ]


def test_seedream_mapper_no_images_no_image_field():
    """无参考图 → body 不含 image 字段。"""
    api = _make_fake_api()
    req = _make_request(model="seedream-5.0")

    captured = {}
    async def _cap(body):
        captured["body"] = body
        return await _make_fake_api().generate(body)
    api.generate = _cap

    asyncio.run(seedream_mapper(req, api))
    assert "image" not in captured["body"]


# ── seedream_mapper:Pro 不接受 Lite-only 字段 ──


def test_seedream_mapper_pro_rejects_sequential_image_generation():
    """Pro 模型传入 sequential_image_generation → RuntimeError。"""
    api = _make_fake_api()
    req = _make_request(model="doubao-seedream-5.0-pro")
    req.params["sequential_image_generation"] = "auto"

    with pytest.raises(RuntimeError, match="Pro 不支持"):
        asyncio.run(seedream_mapper(req, api))


def test_seedream_mapper_pro_rejects_tools():
    """Pro 模型传入 tools → RuntimeError。"""
    api = _make_fake_api()
    req = _make_request(model="doubao-seedream-5.0-pro")
    req.params["tools"] = [{"type": "web_search"}]

    with pytest.raises(RuntimeError, match="Pro 不支持"):
        asyncio.run(seedream_mapper(req, api))


# ── seedream_mapper:返回 NodeOutput ──


def test_seedream_mapper_returns_node_output():
    """mapper 返回 NodeOutput(status='ok', output_type='image')。"""
    api = _make_fake_api()
    req = _make_request(model="seedream-5.0", prompt="test prompt")

    out = asyncio.run(seedream_mapper(req, api))
    assert out.status == "ok"
    assert out.output_type == "image"
    assert isinstance(out.data, bytes)
    assert out.mime_type == "image/png"
    assert out.outputs["image"] is out.data


def test_seedream_mapper_usage_metadata():
    """mapper 返回的 usage 包含 model / size / generated_images。"""
    api = _make_fake_api()
    req = _make_request(model="seedream-5.0", size_mode="4K", ratio="1:1")

    out = asyncio.run(seedream_mapper(req, api))
    assert out.usage["model"] == "seedream-5.0"
    assert out.usage["size"] == "4096x4096"
    assert out.usage["generated_images"] == 1
    assert out.usage["vendor"] == "ark"
