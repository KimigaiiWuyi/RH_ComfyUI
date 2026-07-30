import json
from enum import Enum
from dataclasses import dataclass

from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.core.safe_json import dump_body, mask_body


class _Kind(str, Enum):
    SAMPLE = "sample"


@dataclass
class _Payload:
    name: str
    data: bytes
    kind: _Kind


def test_mask_body_preserves_structure_and_masks_media() -> None:
    data_url = "data:image/png;base64," + ("A" * 12)
    body = {
        "prompt": "猫",
        "image_base64": "AQID",
        "content": [data_url, "A" * 256, "ordinary text"],
        "binary": b"\x89PNG",
    }

    masked = mask_body(body)

    assert masked["prompt"] == "猫"
    assert masked["image_base64"].startswith("<base64://")
    assert masked["image_base64"].endswith("#4>")
    assert masked["content"][0].startswith("data:image/png;base64,<base64://")
    assert masked["content"][1].startswith("<base64://")
    assert masked["content"][1].endswith("#256>")
    assert masked["content"][2] == "ordinary text"
    assert masked["binary"] == "<bytes len=4>"
    assert json.loads(dump_body(body)) == masked


def test_mask_body_serializes_generation_request_without_image_bytes() -> None:
    request = GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt="original prompt",
        images=[b"image-bytes"],
        params={"image_base64": "AQID", "quality": "high"},
        extra={"custom": 1},
    )

    masked = mask_body(request)

    assert masked["task_type"] == "image"
    assert masked["prompt"] == "original prompt"
    assert masked["images"] == ["<bytes len=11>"]
    assert masked["params"]["quality"] == "high"
    assert masked["params"]["image_base64"].startswith("<base64://")
    assert masked["params"]["image_base64"].endswith("#4>")
    assert masked["extra"] == {"custom": 1}


def test_mask_body_handles_dataclass_enum_and_cycles() -> None:
    payload = _Payload(name="test", data=b"123", kind=_Kind.SAMPLE)
    cycle: dict[str, object] = {"payload": payload}
    cycle["self"] = cycle

    masked = mask_body(cycle)

    assert masked["payload"] == {"name": "test", "data": "<bytes len=3>", "kind": "sample"}
    assert masked["self"] == "<circular reference>"
    json.loads(dump_body(cycle))
