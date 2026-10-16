"""可灵视频和图片:鉴权、请求体、轮询和恢复都不打网络。"""

from __future__ import annotations

import hmac
import json
import base64
import asyncio
import hashlib

import httpx
import pytest

from RH_ComfyUI.core import ChannelError, ValidationError
from RH_ComfyUI.utils.core.types import (
    MediaRef,
    MediaKind,
    NodeOutput,
    ContentItem,
    ContentItemType,
    video_ref,
)
from RH_ComfyUI.models.image.defs import ALL_MODELS as IMAGE_MODELS
from RH_ComfyUI.models.video.defs import ALL_MODELS as VIDEO_MODELS
from RH_ComfyUI.models.image.kling import KLING_IMAGE_MODELS
from RH_ComfyUI.models.video.kling import KLING_VIDEO_MODELS
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.kling import config
from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT
from RH_ComfyUI.utils.backends.kling.api import KlingAPI
from RH_ComfyUI.utils.backends.kling.auth import sign_jwt, cached_jwt, clear_jwt_cache, authorization_header
from RH_ComfyUI.core.dispatch.active_tasks import get_active_task_registry
from RH_ComfyUI.utils.backends.kling.parse import (
    parse_poll,
    raise_if_error,
    created_task_id,
    decode_vendor_task,
    encode_vendor_task,
)
from gsuid_core.utils.plugins_config.models import GsStrConfig, GsBoolConfig, GsListStrConfig
from RH_ComfyUI.core.telemetry.wire_capture import get_wire_audit, clear_wire_audit
from RH_ComfyUI.utils.backends.kling.config import endpoint, max_concurrency, is_model_enabled, credentials_ready
from RH_ComfyUI.utils.backends.kling.billing import cny_to_points
from RH_ComfyUI.utils.backends.kling.catalog import (
    DEFAULT_ENDPOINT,
    KLING_MODEL_NAMES,
    KLING_IMAGE_MODELS as IMAGE_CATALOG,
    KLING_VIDEO_MODELS as VIDEO_CATALOG,
    SINGAPORE_ENDPOINT,
)
from RH_ComfyUI.utils.backends.kling.channel import KlingChannel, KlingRuntime
from RH_ComfyUI.utils.backends.kling.payload import KlingSubmit, prepare

_PNG = b"\x89PNG-kling"


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    data: dict[str, object] = {
        "Kling_Enable": True,
        "Kling_API_Key": "kling-key-123456",
        "Kling_Access_Key": "",
        "Kling_Secret_Key": "",
        "Kling_Endpoint": DEFAULT_ENDPOINT,
        "Kling_Max_Concurrency": "2",
        "Kling_Enabled_Models": list(KLING_MODEL_NAMES),
    }
    data.update(overrides)

    def _cfg(key: str) -> object:
        return data[key]

    monkeypatch.setattr(config, "_cfg", _cfg)


def _video(
    prompt: str = "一只猫",
    *,
    images: list[bytes] | None = None,
    duration: int = 5,
    resolution: str | None = None,
    ratio: str | None = None,
    generate_audio: bool = True,
    watermark: bool = False,
    voice_id: str | None = None,
    video_refs: list[MediaRef] | None = None,
    ordered_content: list[ContentItem] | None = None,
    params: dict[str, object] | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=prompt,
        images=list(images or []),
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        generate_audio=generate_audio,
        watermark=watermark,
        voice_id=voice_id,
        video_refs=list(video_refs or []),
        ordered_content=list(ordered_content or []),
        params=params or {},
    )


def _image(
    prompt: str = "一只猫",
    *,
    images: list[bytes] | None = None,
    ratio: str | None = None,
    watermark: bool = False,
    negative_prompt: str = "",
    params: dict[str, object] | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt=prompt,
        images=list(images or []),
        ratio=ratio,
        watermark=watermark,
        negative_prompt=negative_prompt,
        params=params or {},
    )


def _obj(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    out: dict[str, object] = {}
    for key, item in value.items():
        assert isinstance(key, str)
        out[key] = item
    return out


def _items(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [_obj(item) for item in value]


def _settings(submit: KlingSubmit) -> dict[str, object]:
    return _obj(submit.body["settings"])


def _b64() -> str:
    return base64.b64encode(_PNG).decode("ascii")


def test_catalog_matches_console_menus() -> None:
    assert VIDEO_CATALOG == [
        ("kling-3.0-turbo", "Kling 3.0 Turbo"),
        ("kling-3.0", "Kling 3.0 & 3.0 Omni"),
        ("kling-o1", "Kling O1"),
        ("kling-2.6", "Kling 2.6"),
        ("kling-2.5-turbo", "Kling 2.5 Turbo"),
    ]
    assert IMAGE_CATALOG == [
        ("kling-image-3.0", "Kling Image 3.0 & 3.0 Omni"),
        ("kling-image-o1", "Kling Image O1"),
        ("kling-image-common", "通用"),
        ("kling-image-2.1", "Kling Image 2.1"),
    ]
    assert KLING_MODEL_NAMES == [name for name, _title in (*VIDEO_CATALOG, *IMAGE_CATALOG)]


def test_config_group_defaults_off_and_lists_every_model() -> None:
    enabled = SERVICE_CONFIG_DEFAULT["Kling_Enable"]
    assert isinstance(enabled, GsBoolConfig)
    assert enabled.data is False
    chosen = SERVICE_CONFIG_DEFAULT["Kling_Enabled_Models"]
    assert isinstance(chosen, GsListStrConfig)
    assert list(chosen.data) == list(KLING_MODEL_NAMES)
    assert list(chosen.options) == list(KLING_MODEL_NAMES)
    host = SERVICE_CONFIG_DEFAULT["Kling_Endpoint"]
    assert isinstance(host, GsStrConfig)
    assert host.data == DEFAULT_ENDPOINT
    assert host.options == [DEFAULT_ENDPOINT, SINGAPORE_ENDPOINT]
    for key in ("Kling_API_Key", "Kling_Access_Key", "Kling_Secret_Key"):
        item = SERVICE_CONFIG_DEFAULT[key]
        assert isinstance(item, GsStrConfig)
        assert item.secret is True


def test_credentials_and_endpoint_are_read_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, Kling_Enable=False)
    assert credentials_ready() is False
    _patch_cfg(monkeypatch, Kling_API_Key="")
    assert credentials_ready() is False
    _patch_cfg(monkeypatch, Kling_API_Key="", Kling_Access_Key="ak", Kling_Secret_Key="sk")
    assert credentials_ready() is True
    _patch_cfg(monkeypatch, Kling_Endpoint=SINGAPORE_ENDPOINT + "/")
    assert endpoint() == SINGAPORE_ENDPOINT
    _patch_cfg(monkeypatch, Kling_Endpoint="")
    assert endpoint() == DEFAULT_ENDPOINT
    _patch_cfg(monkeypatch, Kling_Enabled_Models=["kling-o1"])
    assert is_model_enabled("kling-o1") is True
    assert is_model_enabled("kling-2.6") is False
    assert is_model_enabled("not-kling") is False
    _patch_cfg(monkeypatch, Kling_Max_Concurrency="0")
    assert max_concurrency() == 1
    _patch_cfg(monkeypatch, Kling_Max_Concurrency="nope")
    assert max_concurrency() == 2


def test_api_key_beats_jwt_and_empty_credentials_omit_header(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_jwt_cache()
    _patch_cfg(monkeypatch, Kling_Access_Key="ak", Kling_Secret_Key="sk")
    assert authorization_header() == "Bearer kling-key-123456"
    _patch_cfg(monkeypatch, Kling_API_Key="", Kling_Access_Key="", Kling_Secret_Key="")
    assert authorization_header() is None
    _patch_cfg(monkeypatch, Kling_API_Key="", Kling_Access_Key="ak-1", Kling_Secret_Key="sk-1")
    token = authorization_header(now=1_700_000_000)
    assert token is not None
    assert token.startswith("Bearer ")
    raw = token.removeprefix("Bearer ")
    header_b, payload_b, signature = raw.split(".")

    def _decode(part: str) -> dict[str, object]:
        padded = part + "=" * (-len(part) % 4)
        found = json.loads(base64.urlsafe_b64decode(padded))
        assert isinstance(found, dict)
        out: dict[str, object] = {}
        for key, item in found.items():
            assert isinstance(key, str)
            out[key] = item
        return out

    header = _decode(header_b)
    payload = _decode(payload_b)
    assert header["alg"] == "HS256"
    assert payload["iss"] == "ak-1"
    assert payload["exp"] == 1_700_000_000 + 1800
    assert payload["nbf"] == 1_700_000_000 - 5
    signing = f"{header_b}.{payload_b}".encode("ascii")
    expect = hmac.new(b"sk-1", signing, hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expect


def test_jwt_cache_refreshes_sixty_seconds_before_expiry() -> None:
    clear_jwt_cache()
    first, exp = sign_jwt("ak", "sk", 1_000)
    assert cached_jwt("ak", "sk", 1_000) == first
    assert cached_jwt("ak", "sk", exp - 61) == first
    refreshed = cached_jwt("ak", "sk", exp - 60)
    assert refreshed != first
    assert cached_jwt("other", "sk", 1_000) != first


def test_turbo_text_and_first_frame_ignore_audio_flag() -> None:
    silent = prepare("kling-3.0-turbo", _video(generate_audio=False, duration=5))
    assert silent.path == "/text-to-video/kling-3.0-turbo"
    assert silent.poll_token == "v"
    assert "audio" not in _settings(silent)
    assert _settings(silent)["aspect_ratio"] == "16:9"
    assert cny_to_points(silent.yuan) == 56
    framed = prepare("kling-3.0-turbo", _video(images=[_PNG], resolution="1080p", duration=15))
    assert framed.path == "/image-to-video/kling-3.0-turbo"
    assert "aspect_ratio" not in _settings(framed)
    kinds = [item["type"] for item in _items(framed.body["contents"])]
    assert kinds == ["prompt", "first_frame"]
    assert _items(framed.body["contents"])[1]["url"] == _b64()
    assert cny_to_points(framed.yuan) == 210


def test_turbo_rejects_tail_video_and_elements() -> None:
    with pytest.raises(ValidationError, match="参考视频"):
        prepare("kling-3.0-turbo", _video(video_refs=[video_ref(url="https://cdn.example/a.mp4")]))
    with pytest.raises(ValidationError, match="最多 1 张"):
        prepare("kling-3.0-turbo", _video(images=[_PNG, _PNG]))
    with pytest.raises(ValidationError, match="主体"):
        prepare("kling-3.0-turbo", _video(params={"element_id": "1"}))


def test_standard_30_routes_text_frames_and_omni() -> None:
    text = prepare("kling-3.0", _video(duration=5, resolution="720p"))
    assert text.path == "/text-to-video/kling-3.0"
    assert _settings(text)["audio"] == "native"
    assert _settings(text)["multi_shot"] is True
    assert cny_to_points(text.yuan) == 63

    quiet = prepare("kling-3.0", _video(generate_audio=False))
    assert _settings(quiet)["audio"] == "off"
    assert cny_to_points(quiet.yuan) == 42

    frames = prepare("kling-3.0", _video(images=[_PNG, _PNG], generate_audio=False))
    assert frames.path == "/image-to-video/kling-3.0"
    assert [item["type"] for item in _items(frames.body["contents"])] == ["prompt", "first_frame", "last_frame"]
    assert "aspect_ratio" not in _settings(frames)

    forced = prepare("kling-3.0", _video(params={"omni": True}))
    assert forced.path == "/omni-video/kling-3.0-omni"
    assert cny_to_points(forced.yuan) == 56

    many = prepare("kling-3.0", _video(images=[_PNG, _PNG, _PNG]))
    assert many.path == "/omni-video/kling-3.0-omni"
    assert [item["type"] for item in _items(many.body["contents"])] == [
        "prompt",
        "first_frame",
        "refer_image",
        "refer_image",
    ]
    assert cny_to_points(many.yuan) == 56

    referred = prepare(
        "kling-3.0",
        _video(
            images=[_PNG],
            video_refs=[video_ref(url="https://cdn.example/a.mp4")],
            params={"video_role": "feature", "element_ids": ["42", "hero"]},
        ),
    )
    assert referred.path == "/omni-video/kling-3.0-omni"
    contents = _items(referred.body["contents"])
    assert [item["type"] for item in contents] == [
        "prompt",
        "first_frame",
        "feature_video",
        "element",
        "element",
    ]
    assert contents[2]["url"] == "https://cdn.example/a.mp4"
    assert contents[3]["element_id"] == 42
    assert contents[4]["element_id"] == "hero"
    assert _settings(referred)["audio"] == "off"
    assert _settings(referred)["multi_shot"] is True
    assert cny_to_points(referred.yuan) == 63


def test_omni_feature_and_base_constraints() -> None:
    with pytest.raises(ValidationError, match="多镜头"):
        prepare(
            "kling-3.0",
            _video(
                video_refs=[video_ref(url="https://cdn.example/a.mp4")],
                params={"video_role": "feature", "multi_shot": False},
            ),
        )
    base = prepare(
        "kling-3.0",
        _video(video_refs=[video_ref(url="https://cdn.example/a.mp4")], params={"video_role": "base"}),
    )
    assert _settings(base)["audio"] == "original"
    assert _settings(base)["multi_shot"] is False
    with pytest.raises(ValidationError, match="待编辑视频"):
        prepare(
            "kling-3.0",
            _video(
                images=[_PNG],
                video_refs=[video_ref(url="https://cdn.example/a.mp4")],
                params={"video_role": "base"},
            ),
        )
    with pytest.raises(ValidationError, match="只接受可访问的 URL"):
        prepare("kling-3.0", _video(video_refs=[video_ref(data=_PNG)]))


def test_o1_audio_duration_and_resolution() -> None:
    text = prepare("kling-o1", _video(duration=10))
    assert text.path == "/omni-video/kling-o1"
    assert _settings(text)["audio"] == "off"
    assert "multi_shot" not in _settings(text)
    assert cny_to_points(text.yuan) == 84
    with pytest.raises(ValidationError, match="时长"):
        prepare("kling-o1", _video(duration=7))
    with pytest.raises(ValidationError, match="4k"):
        prepare("kling-o1", _video(resolution="4k"))
    kept = prepare(
        "kling-o1",
        _video(video_refs=[video_ref(url="https://cdn.example/a.mp4")], duration=7, generate_audio=True),
    )
    assert _settings(kept)["audio"] == "original"
    assert "multi_shot" not in _settings(kept)
    assert cny_to_points(kept.yuan) == 89


def test_v26_audio_resolution_and_voice() -> None:
    spoken = prepare("kling-2.6", _video())
    assert spoken.path == "/text-to-video/kling-2.6"
    assert _settings(spoken)["resolution"] == "1080p"
    assert _settings(spoken)["audio"] == "native"
    assert cny_to_points(spoken.yuan) == 70
    with pytest.raises(ValidationError, match="1080p"):
        prepare("kling-2.6", _video(resolution="720p"))
    with pytest.raises(ValidationError, match="音色"):
        prepare("kling-2.6", _video(voice_id="v1"))
    voiced = prepare("kling-2.6", _video(images=[_PNG], voice_id="v1", params={"voice_ids": ["v2"]}))
    assert voiced.path == "/image-to-video/kling-2.6"
    voices = [item for item in _items(voiced.body["contents"]) if item["type"] == "voice"]
    assert [item["voice_id"] for item in voices] == ["v1", "v2"]
    assert cny_to_points(voiced.yuan) == 84
    frames = prepare("kling-2.6", _video(images=[_PNG, _PNG], generate_audio=False))
    assert _settings(frames)["resolution"] == "1080p"
    assert _settings(frames)["audio"] == "off"
    with pytest.raises(ValidationError, match="不能开声音"):
        prepare("kling-2.6", _video(images=[_PNG, _PNG]))


def test_v25_is_silent_and_tail_frame_needs_1080p() -> None:
    text = prepare("kling-2.5-turbo", _video(generate_audio=True, resolution="720p"))
    assert text.path == "/text-to-video/kling-2.5-turbo"
    assert "audio" not in _settings(text)
    assert cny_to_points(text.yuan) == 21
    frames = prepare("kling-2.5-turbo", _video(images=[_PNG, _PNG], generate_audio=True))
    assert frames.path == "/image-to-video/kling-2.5-turbo"
    assert _settings(frames)["resolution"] == "1080p"
    assert "audio" not in _settings(frames)
    assert cny_to_points(frames.yuan) == 35
    with pytest.raises(ValidationError, match="1080p"):
        prepare("kling-2.5-turbo", _video(images=[_PNG, _PNG], resolution="720p"))


def test_video_options_prompt_limit_and_ordered_media() -> None:
    marked = prepare(
        "kling-2.5-turbo",
        _video(watermark=True, params={"callback_url": "https://hook.example", "external_task_id": "ext-1"}),
    )
    options = _obj(marked.body["options"])
    assert options["watermark_info"] == {"enabled": True}
    assert options["callback_url"] == "https://hook.example"
    assert options["external_task_id"] == "ext-1"
    with pytest.raises(ValidationError, match="3072"):
        prepare("kling-3.0-turbo", _video("猫" * 3073))
    with pytest.raises(ValidationError, match="2500"):
        prepare("kling-2.5-turbo", _video("猫" * 2501))
    ordered = _video(
        ordered_content=[ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, data=_PNG))]
    )
    with pytest.raises(ValidationError, match="images"):
        prepare("kling-2.5-turbo", ordered)


def test_image_30_picks_generations_or_omni() -> None:
    single = prepare("kling-image-3.0", _image())
    assert single.path == "/v1/images/generations"
    assert single.poll_token == "ig"
    assert single.body["model_name"] == "kling-v3"
    assert single.body["resolution"] == "1k"
    assert cny_to_points(single.yuan) == 3
    kept = prepare("kling-image-3.0", _image("看 <<<image_1>>>", images=[_PNG], params={"image_size": "4K"}))
    assert kept.path == "/v1/images/omni-image"
    assert kept.body["model_name"] == "kling-v3-omni"
    assert kept.body["prompt"] == "看 <<<image_1>>>"
    assert cny_to_points(kept.yuan) == 6
    series = prepare("kling-image-3.0", _image(params={"result_type": "series", "series_amount": "auto"}))
    assert series.body["result_type"] == "series"
    assert series.body["series_amount"] == "auto"
    assert "n" not in series.body
    assert cny_to_points(series.yuan) == 12
    with pytest.raises(ValidationError, match="auto"):
        prepare("kling-image-3.0", _image(ratio="auto"))
    followed = prepare("kling-image-3.0", _image(images=[_PNG], ratio="auto"))
    assert followed.path == "/v1/images/omni-image"
    assert followed.body["aspect_ratio"] == "auto"


def test_image_o1_rejects_series_and_4k() -> None:
    submit = prepare("kling-image-o1", _image(params={"n": 2, "image_size": "2K"}))
    assert submit.body["model_name"] == "kling-image-o1"
    assert submit.body["result_type"] == "single"
    assert submit.poll_token == "io"
    assert cny_to_points(submit.yuan) == 6
    with pytest.raises(ValidationError, match="组图"):
        prepare("kling-image-o1", _image(params={"result_type": "series"}))
    with pytest.raises(ValidationError, match="4k"):
        prepare("kling-image-o1", _image(params={"image_size": "4K"}))


def test_image_21_routes_text_subject_and_multi() -> None:
    text = prepare("kling-image-2.1", _image(negative_prompt="模糊"))
    assert text.path == "/v1/images/generations"
    assert text.body["model_name"] == "kling-v2-1"
    assert text.body["negative_prompt"] == "模糊"
    assert cny_to_points(text.yuan) == 2
    subject = prepare("kling-image-2.1", _image(images=[_PNG], params={"image_reference": "subject"}))
    assert subject.body["image_reference"] == "subject"
    assert subject.body["image_fidelity"] == 0.5
    assert subject.body["human_fidelity"] == 0.45
    assert cny_to_points(subject.yuan) == 3
    face = prepare("kling-image-2.1", _image(images=[_PNG], params={"image_reference": "face"}))
    assert "human_fidelity" not in face.body
    multi = prepare("kling-image-2.1", _image("", images=[_PNG, _PNG, _PNG, _PNG]))
    assert multi.path == "/v1/images/multi-image2image"
    assert multi.poll_token == "im"
    assert len(_items(multi.body["subject_image_list"])) == 4
    assert "resolution" not in multi.body
    assert cny_to_points(multi.yuan) == 6
    with pytest.raises(ValidationError, match="负向"):
        prepare("kling-image-2.1", _image(images=[_PNG], negative_prompt="模糊"))
    with pytest.raises(ValidationError, match="多图参考"):
        prepare("kling-image-2.1", _image(images=[_PNG, _PNG], params={"image_reference": "face"}))
    with pytest.raises(ValidationError, match="主体"):
        prepare("kling-image-2.1", _image(params={"element_id": "9"}))
    with pytest.raises(ValidationError, match="必须上传图片"):
        prepare("kling-image-2.1", _image(params={"image_reference": "subject"}))


def test_common_expand_and_subject() -> None:
    expand = prepare("kling-image-common", _image("", images=[_PNG], watermark=True))
    assert expand.path == "/v1/images/editing/expand"
    assert expand.poll_token == "ex"
    assert expand.body["up_expansion_ratio"] == 0.1
    assert _obj(expand.body["watermark_info"])["enabled"] is True
    assert cny_to_points(expand.yuan) == 3
    edge = prepare(
        "kling-image-common",
        _image(
            "",
            images=[_PNG],
            params={
                "up_expansion_ratio": 1,
                "down_expansion_ratio": 1,
                "left_expansion_ratio": 0,
                "right_expansion_ratio": 0,
            },
        ),
    )
    assert edge.body["up_expansion_ratio"] == 1
    with pytest.raises(ValidationError, match="3 倍"):
        prepare(
            "kling-image-common",
            _image(
                "",
                images=[_PNG],
                params={"up_expansion_ratio": 1, "down_expansion_ratio": 1, "left_expansion_ratio": 0.1},
            ),
        )
    subject = prepare(
        "kling-image-common",
        _image("忽略", images=[_PNG], watermark=True, params={"task_mode": "subject"}),
    )
    assert subject.path == "/v1/general/ai-multi-shot"
    assert subject.poll_token == "ms"
    assert subject.body["element_frontal_image"] == _b64()
    assert "prompt" not in subject.body
    assert "watermark_info" not in subject.body
    assert cny_to_points(subject.yuan) == 7
    with pytest.raises(ValidationError, match="1 张"):
        prepare("kling-image-common", _image())


def test_parse_statuses_urls_and_task_prefix() -> None:
    assert encode_vendor_task("ig", "img-1") == "ig:img-1"
    assert decode_vendor_task("ig:img-1") == ("ig", "img-1")
    assert decode_vendor_task("legacy-id") == ("v", "legacy-id")
    assert decode_vendor_task("zz:abc") == ("v", "zz:abc")
    created = created_task_id({"data": {"task_id": "img-9"}})
    assert created == "img-9"
    pending = parse_poll({"data": {"task_status": "processing"}})
    assert pending.done is False
    failed = parse_poll({"data": [{"status": "failed", "message": "不合规"}]})
    assert failed.done is True and failed.failed is True and failed.message == "不合规"
    mixed = parse_poll(
        {
            "data": [
                {
                    "status": "succeeded",
                    "outputs": [
                        {"type": "image", "url": "https://cdn.example/i.png"},
                        {"type": "video", "url": "https://cdn.example/v.mp4"},
                    ],
                }
            ]
        }
    )
    assert mixed.urls == ["https://cdn.example/v.mp4", "https://cdn.example/i.png"]
    subject = parse_poll(
        {
            "data": {
                "task_status": "succeed",
                "task_result": {
                    "images": [{"url_1": "https://cdn.example/s1.png", "url_2": "https://cdn.example/s2.png"}]
                },
            }
        }
    )
    assert subject.urls == ["https://cdn.example/s1.png", "https://cdn.example/s2.png"]


def test_error_envelope_retry_flags() -> None:
    raise_if_error(200, {"code": 0, "data": {}})
    with pytest.raises(ChannelError) as auth_err:
        raise_if_error(200, {"code": 1002, "message": "签名错误"})
    assert auth_err.value.retryable is False
    with pytest.raises(ChannelError) as limit_err:
        raise_if_error(200, {"code": 1303, "message": "限流"})
    assert limit_err.value.retryable is True and limit_err.value.transient is True
    with pytest.raises(ChannelError) as server_err:
        raise_if_error(500, {"code": 5000, "message": "内部错误"})
    assert server_err.value.retryable is True and server_err.value.transient is False
    with pytest.raises(ChannelError) as busy_err:
        raise_if_error(503, {"code": 0, "message": "忙"})
    assert busy_err.value.retryable is True and busy_err.value.transient is True


def test_run_writes_wire_before_post_and_polls_new_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    seen: list[dict[str, object]] = []
    wires: list[dict[str, object]] = []

    class _Resp:
        def __init__(self, payload: dict[str, object]) -> None:
            self.status_code = 200
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def request(
            self,
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
            json: object = None,
            params: object = None,
        ) -> _Resp:
            seen.append({"method": method, "url": url, "headers": dict(headers or {}), "params": params})
            if method == "POST":
                wires.append(get_wire_audit())
                return _Resp({"code": 0, "data": {"id": "task-1", "status": "submitted"}})
            return _Resp(
                {
                    "code": 0,
                    "data": [
                        {"status": "succeeded", "outputs": [{"type": "video", "url": "https://cdn.example/a.mp4"}]}
                    ],
                }
            )

    async def _download(url: str, **kwargs: object) -> bytes:
        assert url == "https://cdn.example/a.mp4"
        return b"mp4-bytes"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.download_with_network_retry", _download)
    submit = prepare("kling-3.0-turbo", _video("猫在跑"))

    async def _go() -> NodeOutput:
        clear_wire_audit()
        return await KlingAPI().run(submit, model_name="kling-3.0-turbo")

    output = asyncio.run(_go())
    assert output.data == b"mp4-bytes"
    assert output.mime_type == "video/mp4"
    assert output.metadata["task_id"] == "v:task-1"
    assert seen[0]["method"] == "POST"
    assert seen[0]["url"] == f"{DEFAULT_ENDPOINT}/text-to-video/kling-3.0-turbo"
    headers = seen[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Bearer kling-key-123456"
    assert seen[1]["url"] == f"{DEFAULT_ENDPOINT}/tasks"
    assert seen[1]["params"] == {"task_ids": "task-1"}
    assert wires and wires[0]["prompt"] == "猫在跑"


def test_empty_credentials_do_not_send_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, Kling_API_Key="", Kling_Access_Key="", Kling_Secret_Key="")
    headers_seen: list[dict[str, str]] = []

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "code": 0,
                "data": {
                    "id": "t",
                    "status": "succeeded",
                    "outputs": [{"type": "video", "url": "https://cdn.example/a.mp4"}],
                },
            }

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def request(
            self, method: str, url: str, headers: dict[str, str] | None = None, **kwargs: object
        ) -> _Resp:
            headers_seen.append(dict(headers or {}))
            return _Resp()

    async def _download(url: str, **kwargs: object) -> bytes:
        return b"ok"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.download_with_network_retry", _download)
    submit = prepare("kling-2.5-turbo", _video())
    asyncio.run(KlingAPI().run(submit, model_name="kling-2.5-turbo"))
    assert headers_seen
    assert "Authorization" not in headers_seen[0]


def test_resume_polls_image_path_without_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    methods: list[str] = []

    async def _exchange(
        self: KlingAPI,
        method: str,
        path: str,
        *,
        json_body: object = None,
        query: object = None,
        timeout: object = None,
    ) -> dict[str, object]:
        methods.append(method)
        assert path == "/v1/general/ai-multi-shot/sub-1"
        assert query is None
        return {
            "code": 0,
            "data": {
                "task_status": "succeed",
                "task_result": {"images": [{"url_1": "https://cdn.example/s1.png"}]},
            },
        }

    async def _download(url: str, **kwargs: object) -> bytes:
        assert url == "https://cdn.example/s1.png"
        return b"png"

    monkeypatch.setattr(KlingAPI, "_exchange", _exchange)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.download_with_network_retry", _download)
    output = asyncio.run(KlingAPI().resume("ms:sub-1", model_name="kling-image-common", kind="image"))
    assert methods == ["GET"]
    assert output.data == b"png"
    assert output.mime_type == "image/png"
    assert output.metadata["task_id"] == "ms:sub-1"


def test_network_and_task_failure_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)

    class _Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Boom:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def request(self, *_args: object, **_kwargs: object) -> object:
            raise httpx.ConnectError("down")

    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.RetryingAsyncClient", _Boom)
    submit = prepare("kling-2.5-turbo", _video())
    with pytest.raises(ChannelError) as net:
        asyncio.run(KlingAPI().run(submit, model_name="kling-2.5-turbo"))
    assert net.value.retryable is True
    assert net.value.code == "PROVIDER_NETWORK_ERROR"

    async def _fail(self: KlingAPI, encoded: str) -> tuple[bool, object]:
        from RH_ComfyUI.utils.backends.kling.parse import KlingPoll

        return True, KlingPoll(done=True, failed=True, message="不合规", urls=[], raw={})

    monkeypatch.setattr(KlingAPI, "poll_once", _fail)

    class _Ok:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Ok:
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def request(self, *_args: object, **_kwargs: object) -> object:
            class _Resp:
                status_code = 200

                def json(self) -> dict[str, object]:
                    return {"code": 0, "data": {"id": "bad", "status": "submitted"}}

            return _Resp()

    monkeypatch.setattr("RH_ComfyUI.utils.backends.kling.api.RetryingAsyncClient", _Ok)
    with pytest.raises(ChannelError) as failed:
        asyncio.run(KlingAPI().run(submit, model_name="kling-2.5-turbo"))
    assert failed.value.retryable is False
    assert failed.value.code == "TASK_FAILED"


def test_channel_gates_and_shared_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, Kling_Max_Concurrency="1")
    runtime = KlingRuntime()
    video = KlingChannel("kling-3.0-turbo", runtime=runtime)
    image = KlingChannel("kling-image-3.0", runtime=runtime)
    assert video.name == "kling" and video.weight == 5
    assert video.supports_remote_cancel() is False

    async def _ready() -> tuple[bool, bool]:
        return await video.check_available(), await image.check_available()

    assert asyncio.run(_ready()) == (True, True)
    runtime.inflight = 1
    assert asyncio.run(_ready()) == (False, False)

    async def _reason() -> str:
        return await video.unavailable_reason()

    assert "并发" in asyncio.run(_reason())
    _patch_cfg(monkeypatch, Kling_Enable=False)
    assert "未配置" in asyncio.run(_reason())
    _patch_cfg(monkeypatch, Kling_Enabled_Models=["kling-o1"])

    async def _image_reason() -> str:
        return await image.unavailable_reason()

    assert "未勾选" in asyncio.run(_image_reason())


def test_invoke_keeps_validation_and_releases_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    runtime = KlingRuntime()
    channel = KlingChannel("kling-3.0-turbo", runtime=runtime)
    with pytest.raises(ValidationError):
        asyncio.run(channel.invoke(request=_video("")))
    assert runtime.inflight == 0

    async def _boom(submit: KlingSubmit, *, model_name: str) -> NodeOutput:
        assert runtime.inflight == 1
        assert submit.path == "/text-to-video/kling-3.0-turbo"
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime.api, "run", _boom)
    with pytest.raises(ChannelError) as wrapped:
        asyncio.run(channel.invoke(request=_video("猫")))
    assert wrapped.value.retryable is True
    assert runtime.inflight == 0


def test_models_register_with_menu_names_and_estimates() -> None:
    expected = {
        "kling-3.0-turbo": ("Kling 3.0 Turbo", 56),
        "kling-3.0": ("Kling 3.0 & 3.0 Omni", 63),
        "kling-o1": ("Kling O1", 42),
        "kling-2.6": ("Kling 2.6", 70),
        "kling-2.5-turbo": ("Kling 2.5 Turbo", 21),
    }
    for cls in KLING_VIDEO_MODELS:
        model = cls()
        title, points = expected[model.name]
        assert model.display_name == title
        assert model.node.backend == "kling"
        assert model.execution_mode == "async_poll"
        assert model.node.requirements == ["Kling_API_Key"]
        assert cls in VIDEO_MODELS
        low, high = model.point_range()
        assert low < high
        assert model.estimate_cost(_video()) == points
        binding = model.channel_bindings()[0]
        assert binding.channel.name == "kling"
        assert binding.channel.supports_remote_cancel() is False
    image_expected = {
        "kling-image-3.0": "Kling Image 3.0 & 3.0 Omni",
        "kling-image-o1": "Kling Image O1",
        "kling-image-common": "通用",
        "kling-image-2.1": "Kling Image 2.1",
    }
    for cls in KLING_IMAGE_MODELS:
        model = cls()
        assert model.display_name == image_expected[model.name]
        assert model.node.backend == "kling"
        assert cls in IMAGE_MODELS
        low, high = model.point_range()
        assert low < high
    image30 = KLING_IMAGE_MODELS[0]()
    assert image30.estimate_cost(_image()) == 3
    assert image30.estimate_cost(_image(params={"image_size": "4K"})) == 6
    image21 = KLING_IMAGE_MODELS[3]()
    assert image21.estimate_cost(_image()) == 2
    assert image21.estimate_cost(_image(images=[_PNG])) == 3
    assert image21.estimate_cost(_image(images=[_PNG, _PNG])) == 6
    common = KLING_IMAGE_MODELS[2]()
    common.validate(_image("", images=[_PNG]))
    assert common.estimate_cost(_image("", images=[_PNG], params={"task_mode": "subject"})) == 7
    turbo = KLING_VIDEO_MODELS[0]()
    turbo.validate(_video("猫"))
    with pytest.raises(ValidationError):
        turbo.validate(_video("猫", video_refs=[video_ref(url="https://cdn.example/a.mp4")]))


def test_resume_inference_and_poll_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from RH_ComfyUI.api import GenerationResult
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    assert resume_mod.can_resume(backend="kling", vendor_task_id="v:1") is True
    assert resume_mod.can_resume(model="kling-image-o1", vendor_task_id="io:1") is True
    assert resume_mod.can_resume(channel="kling", vendor_task_id="v:1") is True
    assert resume_mod.can_resume(backend="kling", vendor_task_id="") is False
    assert resume_mod._infer_backend(backend="", model="kling-2.6", channel="") == "kling"
    assert resume_mod._kind_for_model("kling-image-common", "") == "image"
    assert resume_mod._kind_for_model("kling-o1", "kling") == "video"
    assert resume_mod._channel_implies_backend("kling") == "kling"

    calls: list[tuple[str, str, str]] = []

    async def _fake_resume(self: KlingAPI, encoded: str, *, model_name: str, kind: str) -> NodeOutput:
        ag = get_active_task_registry().current()
        assert ag is not None
        assert ag.allow_cancel is False
        assert ag.cancel_remote is None
        assert ag.vendor_task_id == encoded
        calls.append((encoded, model_name, kind))
        return NodeOutput(data=b"vid", mime_type="video/mp4", output_type="video")

    monkeypatch.setattr(KlingAPI, "resume", _fake_resume)

    async def _go() -> GenerationResult:
        return await resume_mod.resume_poll(
            model="kling-3.0",
            vendor_task_id="v:task-9",
            backend="kling",
            update_record=False,
        )

    result = asyncio.run(_go())
    assert calls == [("v:task-9", "kling-3.0", "video")]
    assert result.backend == "kling"
    assert result.data == b"vid"
    assert result.metadata["resumed"] is True

    async def _fail(self: KlingAPI, encoded: str, *, model_name: str, kind: str) -> NodeOutput:
        raise ChannelError("不合规", code="TASK_FAILED", user_message="不合规", retryable=False)

    monkeypatch.setattr(KlingAPI, "resume", _fail)
    finals: list[str] = []

    async def _finalize(record_id: int | None, *, status: str, error: str = "", elapsed_ms: int = 0) -> None:
        finals.append(status)

    monkeypatch.setattr(resume_mod, "_finalize_record", _finalize)

    async def _failed() -> None:
        with pytest.raises(resume_mod.ResumeFailedError) as exc:
            await resume_mod.resume_poll(
                model="kling-image-3.0",
                vendor_task_id="ig:img",
                kind="image",
                record_id=7,
            )
        assert exc.value.definitive is True

    asyncio.run(_failed())
    assert finals == ["failed"]
