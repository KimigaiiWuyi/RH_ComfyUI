"""submit.channel 钉扎:顶层字段、auto、未知名称、params 回退。"""

import asyncio

import pytest

from RH_ComfyUI.core.base.errors import ValidationError
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.base.generation import normalize_channel_pin, requested_channel_name


def test_normalize_channel_pin_treats_auto_as_unset():
    assert normalize_channel_pin(None) is None
    assert normalize_channel_pin("") is None
    assert normalize_channel_pin("  ") is None
    assert normalize_channel_pin("auto") is None
    assert normalize_channel_pin("AUTO") is None
    assert normalize_channel_pin(" ark ") == "ark"


def test_requested_channel_prefers_top_level_over_params():
    req = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="x",
        channel="ark",
        params={"channel": "runninghub"},
    )
    assert requested_channel_name(req) == "ark"


def test_requested_channel_falls_back_to_params():
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="x", params={"channel": "gemini"})
    assert requested_channel_name(req) == "gemini"


def test_build_request_puts_channel_on_dataclass_not_params():
    from RH_ComfyUI.api import _build_request

    req = asyncio.run(_build_request(task_type="image", prompt="cat", kwargs={"channel": "ark", "image_size": "2K"}))
    assert req.channel == "ark"
    assert "channel" not in req.params
    assert req.params["image_size"] == "2K"


def test_resolve_channel_pin_unknown_model():
    from RH_ComfyUI.api import resolve_channel_pin

    with pytest.raises(ValidationError, match="未知模型"):
        asyncio.run(resolve_channel_pin("not-a-real-model", "ark"))


def test_resolve_channel_pin_auto_skips_lookup():
    from RH_ComfyUI.api import resolve_channel_pin

    assert asyncio.run(resolve_channel_pin("not-a-real-model", "auto")) is None
    assert asyncio.run(resolve_channel_pin("not-a-real-model", None)) is None
