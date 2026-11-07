"""fal FLUX 3 Max:计费、请求体、配置闸、解析、轮询和恢复都不打网络。"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from RH_ComfyUI.core import ChannelError, ValidationError
from RH_ComfyUI.utils.core.types import NodeOutput, ContentItem, ContentItemType
from RH_ComfyUI.models.video.defs import ALL_MODELS as VIDEO_MODELS
from RH_ComfyUI.utils.backends.fal import config
from RH_ComfyUI.utils.core.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.fal.api import FalAPI
from RH_ComfyUI.utils.backends.fal.parse import parse_status, video_url_of, request_id_of, raise_if_error
from RH_ComfyUI.utils.backends.fal.billing import video_usd, usd_to_points, video_point_bounds
from RH_ComfyUI.utils.backends.fal.catalog import (
    APP_PREFIX,
    ENDPOINT_IDS,
    FAL_MODEL_NAMES,
    DEFAULT_QUEUE_ENDPOINT,
)
from RH_ComfyUI.utils.backends.fal.channel import FalChannel, FalRuntime
from RH_ComfyUI.utils.backends.fal.payload import prepare
from RH_ComfyUI.core.telemetry.wire_capture import get_wire_audit, clear_wire_audit

_PNG = b"\x89PNG-fal"


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    data: dict[str, object] = {
        "FAL_Enable": True,
        "FAL_API_Key": "fal-key-123456",
        "FAL_Endpoint": DEFAULT_QUEUE_ENDPOINT,
        "FAL_Max_Concurrency": "2",
        "FAL_Enabled_Models": list(FAL_MODEL_NAMES),
    }
    data.update(overrides)

    def _cfg(key: str) -> object:
        return data[key]

    monkeypatch.setattr(config, "_cfg", _cfg)


def _video(
    prompt: str = "一只猫在月球上奔跑",
    *,
    images: list[bytes] | None = None,
    duration: int = 5,
    resolution: str | None = None,
    ratio: str | None = None,
    params: dict[str, object] | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=prompt,
        images=list(images or []),
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        params=dict(params or {}),
    )


# ── 计费 ──


def test_usd_to_points_rounds_up_and_keeps_at_least_one() -> None:
    assert usd_to_points(Decimal("0.85")) == 85
    assert usd_to_points(Decimal("5.80")) == 580
    assert usd_to_points(Decimal("0.001")) == 1
    assert usd_to_points(Decimal("0")) == 1


def test_video_usd_matches_published_list_rates() -> None:
    # 刊例价:720p $0.17/s,1080p $0.29/s
    assert video_usd("720p", 5) == Decimal("0.85")
    assert video_usd("720p", 10) == Decimal("1.70")
    assert video_usd("1080p", 5) == Decimal("1.45")
    assert video_usd("1080p", 20) == Decimal("5.80")


def test_video_point_bounds_cover_full_range() -> None:
    low, high = video_point_bounds()
    assert (low, high) == (85, 580)
    assert low < high


def test_billing_rejects_unknown_resolution_and_nonpositive_seconds() -> None:
    with pytest.raises(ValueError):
        video_usd("2k", 5)
    with pytest.raises(ValueError):
        video_usd("4k", 5)
    with pytest.raises(ValueError):
        video_usd("720p", 0)


# ── 配置闸 ──


def test_config_reads_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, FAL_Enable=False)
    assert config.credentials_ready() is False
    _patch_cfg(monkeypatch, FAL_API_Key="")
    assert config.credentials_ready() is False
    _patch_cfg(monkeypatch)
    assert config.credentials_ready() is True
    assert config.endpoint() == DEFAULT_QUEUE_ENDPOINT
    _patch_cfg(monkeypatch, FAL_Endpoint="")
    assert config.endpoint() == DEFAULT_QUEUE_ENDPOINT
    _patch_cfg(monkeypatch, FAL_Endpoint=DEFAULT_QUEUE_ENDPOINT + "/")
    assert config.endpoint() == DEFAULT_QUEUE_ENDPOINT
    _patch_cfg(monkeypatch, FAL_Enabled_Models=[])
    assert config.is_model_enabled("flux3max") is False
    _patch_cfg(monkeypatch, FAL_Enabled_Models=["flux3max"])
    assert config.is_model_enabled("flux3max") is True
    _patch_cfg(monkeypatch, FAL_Max_Concurrency="nope")
    assert config.max_concurrency() == 2
    _patch_cfg(monkeypatch, FAL_Max_Concurrency="0")
    assert config.max_concurrency() == 1


# ── 请求体 / 校验 ──


def test_t2v_body_fields() -> None:
    submit = prepare("flux3max", _video(duration=10, resolution="1080p", ratio="16:9"))
    assert submit.mode == "text_to_video"
    assert submit.path == f"/{ENDPOINT_IDS['text_to_video']}"
    body = submit.body
    assert body["prompt"] == "一只猫在月球上奔跑"
    assert body["duration"] == 10
    assert body["resolution"] == "1080p"
    assert body["aspect_ratio"] == "16:9"
    assert body["reasoning_level"] == "high"
    assert body["generate_audio"] is True
    assert "image_url" not in body
    assert "keyframes" not in body
    assert "seed" not in body
    assert submit.usd == Decimal("2.90")


def test_i2v_body_uses_image_url_and_optional_end_image() -> None:
    one = prepare("flux3max", _video(images=[_PNG]))
    assert one.mode == "image_to_video"
    assert one.path == f"/{ENDPOINT_IDS['image_to_video']}"
    assert str(one.body["image_url"]).startswith("data:image/png;base64,")
    assert "end_image_url" not in one.body
    assert "keyframes" not in one.body

    two = prepare("flux3max", _video(images=[_PNG, _PNG]))
    assert two.mode == "image_to_video"
    assert str(two.body["end_image_url"]).startswith("data:image/png;base64,")


def test_three_plus_images_auto_keyframes_with_even_indices() -> None:
    submit = prepare("flux3max", _video(images=[_PNG, _PNG, _PNG], duration=10))
    assert submit.mode == "keyframes_to_video"
    keyframes = submit.body["keyframes"]
    assert isinstance(keyframes, list)
    indices = [k["frame_index"] for k in keyframes if isinstance(k, dict)]
    assert indices == [0, 120, 240]


def test_explicit_task_mode_locks_and_validates() -> None:
    sub = prepare("flux3max", _video(params={"task_mode": "keyframes_to_video"}, images=[_PNG]))
    assert sub.mode == "keyframes_to_video"
    with pytest.raises(ValidationError, match="不能带参考图"):
        prepare("flux3max", _video(params={"task_mode": "text_to_video"}, images=[_PNG]))
    with pytest.raises(ValidationError, match="1~2 张"):
        prepare("flux3max", _video(params={"task_mode": "image_to_video"}))
    with pytest.raises(ValidationError, match="至少 1 张"):
        prepare("flux3max", _video(params={"task_mode": "keyframes_to_video"}))
    with pytest.raises(ValidationError, match="task_mode"):
        prepare("flux3max", _video(params={"task_mode": "nope"}))


def test_frame_indices_validation() -> None:
    ok = prepare(
        "flux3max",
        _video(images=[_PNG, _PNG], params={"frame_indices": [120, 0]}, duration=5),
    )
    pairs = ok.body["keyframes"]
    assert isinstance(pairs, list)
    assert [k["frame_index"] for k in pairs if isinstance(k, dict)] == [0, 120]

    with pytest.raises(ValidationError, match="一一对应"):
        prepare("flux3max", _video(images=[_PNG, _PNG], params={"frame_indices": [0]}))
    with pytest.raises(ValidationError, match="唯一"):
        prepare("flux3max", _video(images=[_PNG, _PNG], params={"frame_indices": [0, 0]}))
    with pytest.raises(ValidationError, match="超出"):
        prepare("flux3max", _video(images=[_PNG], params={"frame_indices": [121]}, duration=5))
    with pytest.raises(ValidationError, match="整数列表"):
        prepare("flux3max", _video(images=[_PNG], params={"frame_indices": "0"}))


def test_frame_indices_without_images_means_keyframes() -> None:
    sub = prepare("flux3max", _video(params={"frame_indices": [0, 120]}, images=[_PNG, _PNG]))
    assert sub.mode == "keyframes_to_video"


def test_duration_and_resolution_and_ratio_validation() -> None:
    import json
    from typing import Any

    with pytest.raises(ValidationError, match="5~20"):
        prepare("flux3max", _video(duration=4))
    with pytest.raises(ValidationError, match="5~20"):
        prepare("flux3max", _video(duration=21))
    bad: Any = json.loads('"abc"')
    bad_req = GenerationRequest(task_type=TaskType.VIDEO, prompt="x", duration=bad)
    with pytest.raises(ValidationError, match="整数秒"):
        prepare("flux3max", bad_req)
    submit = prepare("flux3max", _video(duration=20))
    assert submit.body["duration"] == 20

    with pytest.raises(ValidationError, match="720p/1080p"):
        prepare("flux3max", _video(resolution="2K"))
    assert prepare("flux3max", _video(resolution="1080p")).body["resolution"] == "1080p"

    with pytest.raises(ValidationError, match="宽高比"):
        prepare("flux3max", _video(ratio="3:2"))
    assert prepare("flux3max", _video()).body["aspect_ratio"] == "auto"


def test_other_payload_constraints() -> None:
    with pytest.raises(ValidationError, match="提示词"):
        prepare("flux3max", _video(""))
    # for_estimate 允许空 prompt
    assert prepare("flux3max", _video(""), for_estimate=True).body["prompt"] == "(estimate)"

    with pytest.raises(ValidationError, match="最多 10"):
        prepare("flux3max", _video(images=[_PNG] * 11))
    with pytest.raises(ValidationError, match="空的"):
        prepare("flux3max", _video(images=[b""]))
    with pytest.raises(ValidationError, match="reasoning_level"):
        prepare("flux3max", _video(params={"reasoning_level": "max"}))

    req = _video(params={"seed": 7, "reasoning_level": "low"})
    req.seed = 7
    sub = prepare("flux3max", req)
    assert sub.body["seed"] == 7
    assert sub.body["reasoning_level"] == "low"

    oc = _video()
    oc.ordered_content = [ContentItem(type=ContentItemType.TEXT, text="hi")]
    with pytest.raises(ValidationError, match="多模态交错"):
        prepare("flux3max", oc)

    vr = _video()
    from RH_ComfyUI.utils.core.types import MediaRef, MediaKind

    vr.video_refs = [MediaRef(kind=MediaKind.VIDEO, data=b"x")]
    with pytest.raises(ValidationError, match="参考视频"):
        prepare("flux3max", vr)


# ── 解析 ──


def test_parse_status_pending_done_failed() -> None:
    pending = parse_status({"status": "IN_QUEUE", "queue_position": 3})
    assert pending.done is False and "第 3 位" in pending.message
    assert parse_status({"status": "IN_PROGRESS"}).done is False

    done = parse_status({"status": "COMPLETED", "request_id": "r1"})
    assert done.done and not done.failed

    failed = parse_status({"status": "COMPLETED", "error": "content policy"})
    assert failed.done and failed.failed and "policy" in failed.message

    cancelled = parse_status({"status": "CANCELLED"})
    assert cancelled.done and cancelled.failed


def test_request_id_and_video_url_extraction() -> None:
    assert request_id_of({"request_id": "abc"}) == "abc"
    with pytest.raises(ChannelError, match="request_id"):
        request_id_of({})

    assert video_url_of({"video": {"url": "https://cdn.example/a.mp4"}}) == "https://cdn.example/a.mp4"
    assert video_url_of({"payload": {"video": {"url": "https://cdn.example/b.mp4"}}}) == ("https://cdn.example/b.mp4")
    with pytest.raises(ChannelError, match="video.url"):
        video_url_of({"video": {}})


def test_raise_if_error_classifies_http() -> None:
    raise_if_error(200, {}, stage="create")

    with pytest.raises(ChannelError) as auth:
        raise_if_error(401, {"detail": "unauthorized"}, stage="create")
    assert auth.value.resolved_kind.name == "AUTH_OR_QUOTA"

    with pytest.raises(ChannelError) as rate:
        raise_if_error(429, {"detail": "slow down"}, stage="create")
    assert rate.value.resolved_kind.name == "RATE_LIMITED"
    assert rate.value.retryable is True

    with pytest.raises(ChannelError) as invalid:
        raise_if_error(422, {"detail": [{"msg": "bad enum"}]}, stage="create")
    assert invalid.value.resolved_kind.name == "INVALID_REQUEST"
    assert "bad enum" in str(invalid.value)


# ── 通道 ──


def test_channel_gates_and_shared_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, FAL_Max_Concurrency="1")
    runtime = FalRuntime()
    a = FalChannel("flux3max", runtime=runtime)
    b = FalChannel("flux3max", runtime=runtime)

    async def _ready() -> tuple[bool, bool]:
        return await a.check_available(), await b.check_available()

    assert asyncio.run(_ready()) == (True, True)
    runtime.inflight = 1
    assert asyncio.run(_ready()) == (False, False)

    async def _reason() -> str:
        return await a.unavailable_reason()

    assert "并发" in asyncio.run(_reason())
    _patch_cfg(monkeypatch, FAL_Enable=False)
    assert "FAL_Enable" in asyncio.run(_reason())
    _patch_cfg(monkeypatch, FAL_Enabled_Models=[])
    assert "FAL_Enabled_Models" in asyncio.run(_reason())
    assert a.supports_remote_cancel() is True
    assert a.audit_key_prefix()


def test_invoke_keeps_validation_and_releases_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    runtime = FalRuntime()
    channel = FalChannel("flux3max", runtime=runtime)
    with pytest.raises(ValidationError):
        asyncio.run(channel.invoke(request=_video("")))
    assert runtime.inflight == 0

    async def _boom(*args: object, **kwargs: object) -> NodeOutput:
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime.api, "run", _boom)
    with pytest.raises(ChannelError):
        asyncio.run(channel.invoke(request=_video("猫")))
    assert runtime.inflight == 0


# ── API 生命周期(假 HTTP)──


class _Resp:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> object:
        return self._payload


def test_run_writes_wire_before_post_polls_and_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    seen: list[dict[str, object]] = []
    wires: list[dict[str, object]] = []

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None, json: object = None) -> _Resp:
            seen.append({"method": "POST", "url": url, "headers": dict(headers or {}), "json": json})
            wires.append(get_wire_audit())
            return _Resp({"request_id": "req-1", "status": "IN_QUEUE"})

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            seen.append({"method": "GET", "url": url})
            if url.endswith("/status"):
                return _Resp({"status": "COMPLETED", "request_id": "req-1"})
            return _Resp({"video": {"url": "https://cdn.example/a.mp4"}, "seed": 1})

    async def _download(url: str, **kwargs: object) -> bytes:
        assert url == "https://cdn.example/a.mp4"
        return b"mp4-bytes"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.download_with_network_retry", _download)
    submit = prepare("flux3max", _video("猫在跑"))

    async def _go() -> NodeOutput:
        clear_wire_audit()
        return await FalAPI().run(submit, model_name="flux3max")

    output = asyncio.run(_go())
    assert output.data == b"mp4-bytes"
    assert output.mime_type == "video/mp4"
    assert output.metadata["task_id"] == "req-1"
    assert output.usage["request_id"] == "req-1"
    assert seen[0]["method"] == "POST"
    assert seen[0]["url"] == f"{DEFAULT_QUEUE_ENDPOINT}/blackforestlabs/flux-3-max/text-to-video"
    headers = seen[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["Authorization"] == "Key fal-key-123456"
    assert seen[1]["url"] == f"{DEFAULT_QUEUE_ENDPOINT}/{APP_PREFIX}/requests/req-1/status"
    assert seen[2]["url"] == f"{DEFAULT_QUEUE_ENDPOINT}/{APP_PREFIX}/requests/req-1"
    assert wires and wires[0]["prompt"] == "猫在跑"


def test_run_binds_vendor_task_with_remote_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    bound: list[dict[str, object]] = []

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None, json: object = None) -> _Resp:
            return _Resp({"request_id": "req-9"})

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            if url.endswith("/status"):
                return _Resp({"status": "COMPLETED"})
            return _Resp({"video": {"url": "https://cdn.example/a.mp4"}})

    async def _download(url: str, **kwargs: object) -> bytes:
        return b"ok"

    from RH_ComfyUI.core.dispatch.active_tasks import get_active_task_registry

    registry = get_active_task_registry()

    async def _bind(**kwargs: object) -> None:
        bound.append(dict(kwargs))

    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.download_with_network_retry", _download)
    monkeypatch.setattr(registry, "bind_vendor_cancel", _bind)

    submit = prepare("flux3max", _video())
    asyncio.run(FalAPI().run(submit, model_name="flux3max"))
    assert bound and bound[0]["vendor_task_id"] == "req-9"
    assert bound[0]["channel_name"] == "fal"
    assert callable(bound[0]["cancel_remote"])


def test_resume_polls_without_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    calls: list[str] = []

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, **kwargs: object) -> _Resp:
            raise AssertionError("resume 不得重新建单")

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            calls.append(url)
            if url.endswith("/status"):
                return _Resp({"status": "COMPLETED"})
            return _Resp({"video": {"url": "https://cdn.example/a.mp4"}})

    async def _download(url: str, **kwargs: object) -> bytes:
        return b"resumed"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.download_with_network_retry", _download)
    output = asyncio.run(FalAPI().resume("req-7", model_name="flux3max"))
    assert output.data == b"resumed"
    assert calls[0].endswith("/requests/req-7/status")


def test_empty_credentials_do_not_send_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch, FAL_API_Key="")
    seen: list[dict[str, str]] = []

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None, json: object = None) -> _Resp:
            seen.append(dict(headers or {}))
            return _Resp({"request_id": "r"})

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            if url.endswith("/status"):
                return _Resp({"status": "COMPLETED"})
            return _Resp({"video": {"url": "https://cdn.example/a.mp4"}})

    async def _download(url: str, **kwargs: object) -> bytes:
        return b"ok"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.download_with_network_retry", _download)
    submit = prepare("flux3max", _video())
    asyncio.run(FalAPI().run(submit, model_name="flux3max"))
    assert "Authorization" not in seen[0]


# ── 模型 ──


def test_model_registered_and_schema() -> None:
    names = {cls.node_def().name for cls in VIDEO_MODELS}
    assert "flux3max" in names

    from RH_ComfyUI.models.video.fal import Flux3MaxVideo

    model = Flux3MaxVideo()
    node = model.node
    assert node.backend == "fal"
    assert node.task_type == TaskType.VIDEO
    assert "task_mode" in node.inputs
    assert "frame_indices" in node.inputs
    task_mode = node.inputs["task_mode"]
    assert set(task_mode.values or []) == {"auto", "text_to_video", "image_to_video", "keyframes_to_video"}
    res = node.inputs["resolution"]
    assert set(res.values or []) == {"720p", "1080p"}
    assert model.point_cost == 85

    low, high = model.point_range()
    assert (low, high) == (85, 580)
    assert low < high


def test_model_estimate_cost_follows_params() -> None:
    from RH_ComfyUI.models.video.fal import Flux3MaxVideo

    model = Flux3MaxVideo()
    assert model.estimate_cost(_video()) == 85
    assert model.estimate_cost(_video(resolution="1080p")) == 145
    assert model.estimate_cost(_video(resolution="1080p", duration=20)) == 580
    # 双轨:params['resolution'] 在顶层为空时生效;顶层 duration 缺省 5 秒
    req = _video(params={"resolution": "1080p"})
    req.resolution = None
    assert model.estimate_cost(req) == 145
    req2 = _video(params={"resolution": "1080p", "duration": 10}, duration=10)
    assert model.estimate_cost(req2) == 290


def test_model_validate_runs_prepare() -> None:
    from RH_ComfyUI.models.video.fal import Flux3MaxVideo

    model = Flux3MaxVideo()
    model.validate(_video())
    # 3+ 图(关键帧)走 MULTIMODAL 形态,须通过基类形态闸
    model.validate(_video(images=[_PNG, _PNG, _PNG]))
    with pytest.raises(ValidationError):
        model.validate(_video(resolution="4K"))
    with pytest.raises(ValidationError):
        model.validate(_video(images=[_PNG, _PNG, _PNG], params={"task_mode": "image_to_video"}))


def test_channel_bindings_present() -> None:
    from RH_ComfyUI.models.video.fal import Flux3MaxVideo

    bindings = Flux3MaxVideo().channel_bindings()
    assert bindings
    assert isinstance(bindings[0].channel, FalChannel)
    assert bindings[0].vendor_model == APP_PREFIX


def test_service_config_keys_present() -> None:
    from RH_ComfyUI.rh_config.service_config import SERVICE_CONFIG_DEFAULT

    for key in ("FAL_Enable", "FAL_Enabled_Models", "FAL_API_Key", "FAL_Endpoint", "FAL_Max_Concurrency"):
        assert key in SERVICE_CONFIG_DEFAULT, key
