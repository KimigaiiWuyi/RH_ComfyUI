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
    assert usd_to_points(Decimal("0.85")) == 595
    assert usd_to_points(Decimal("5.80")) == 4060
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
    assert (low, high) == (595, 4060)
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
    # invoke 层的组包错误统一翻译成 CAPABILITY(换源),且不占并发
    with pytest.raises(ChannelError) as exc:
        asyncio.run(channel.invoke(request=_video("")))
    assert runtime.inflight == 0
    from RH_ComfyUI.core.channels.failure import FailureKind

    assert exc.value.kind == FailureKind.CAPABILITY

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
    assert model.point_cost == 595

    low, high = model.point_range()
    assert (low, high) == (595, 4060)
    assert low < high


def test_model_estimate_cost_follows_params() -> None:
    from RH_ComfyUI.models.video.fal import Flux3MaxVideo

    model = Flux3MaxVideo()
    assert model.estimate_cost(_video()) == 595
    assert model.estimate_cost(_video(resolution="1080p")) == 1015
    assert model.estimate_cost(_video(resolution="1080p", duration=20)) == 4060
    # 双轨:params['resolution'] 在顶层为空时生效;顶层 duration 缺省 5 秒
    req = _video(params={"resolution": "1080p"})
    req.resolution = None
    assert model.estimate_cost(req) == 1015
    req2 = _video(params={"resolution": "1080p", "duration": 10}, duration=10)
    assert model.estimate_cost(req2) == 2030


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


# ═══════════════════════════════════════════════════════════
#  MiniMax H3 Max / H3 Max Turbo
# ═══════════════════════════════════════════════════════════

from RH_ComfyUI.utils.core.types import MediaRef, MediaKind  # noqa: E402
from RH_ComfyUI.utils.backends.fal.billing import (  # noqa: E402
    H3_REF_TOKENS_INCLUDED,
    h3_video_usd,
    h3_point_bounds,
    h3_ref_tokens_usd,
)


def _h3v(
    prompt: str = "一只猫在月球上奔跑",
    *,
    images: list[bytes] | None = None,
    video_refs: list[MediaRef] | None = None,
    audio_refs: list[MediaRef] | None = None,
    ordered: list[ContentItem] | None = None,
    duration: int = 5,
    resolution: str | None = None,
    ratio: str | None = None,
    params: dict[str, object] | None = None,
) -> GenerationRequest:
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt=prompt,
        images=list(images or []),
        video_refs=list(video_refs or []),
        audio_refs=list(audio_refs or []),
        duration=duration,
        resolution=resolution,
        ratio=ratio,
        params=dict(params or {}),
    )
    if ordered is not None:
        req.ordered_content = list(ordered)
    return req


# ── H3 计费 ──


def test_h3_video_usd_matches_list_rates() -> None:
    # 刊例价:h3max 480p/768p/1080p = 0.05/0.08/0.16;turbo 半价
    assert h3_video_usd("h3max", "text_to_video", "768p", 5) == Decimal("0.40")
    assert h3_video_usd("h3max", "image_to_video", "1080p", 15) == Decimal("2.40")
    assert h3_video_usd("h3maxturbo", "text_to_video", "480p", 5) == Decimal("0.125")
    assert h3_video_usd("h3maxturbo", "text_to_video", "768p", 5) == Decimal("0.20")
    assert h3_video_usd("h3maxturbo", "image_to_video", "1080p", 10) == Decimal("0.80")
    # reference 两模型同按 H3 Max 档
    assert h3_video_usd("h3maxturbo", "reference_to_video", "768p", 5) == Decimal("0.40")
    assert h3_video_usd("h3max", "reference_to_video", "768p", 5) == Decimal("0.40")


def test_h3_ref_tokens_usd_overage() -> None:
    assert h3_ref_tokens_usd("h3max", H3_REF_TOKENS_INCLUDED) == Decimal("0")
    assert h3_ref_tokens_usd("h3max", 0) == Decimal("0")
    # 2048 超量 × $0.02/1K = $0.04096
    assert h3_ref_tokens_usd("h3max", 6144) == Decimal("0.04096")
    # turbo 档 $0.016/1K
    assert h3_ref_tokens_usd("h3maxturbo", 6144) == Decimal("0.032768")


def test_h3_point_bounds() -> None:
    low_t, high_t = h3_point_bounds("h3maxturbo")
    low_m, high_m = h3_point_bounds("h3max")
    assert low_t < high_t and low_m < high_m
    assert low_t < low_m  # turbo 起步价更低
    assert high_m == 1680  # 0.16 × 15s × 700


# ── H3 请求体 / 校验 ──


def test_h3_t2v_body_fields() -> None:
    submit = prepare("h3max", _h3v(duration=7, resolution="1080p", ratio="16:9"))
    assert submit.mode == "text_to_video"
    assert submit.app == "minimax/h3-max"
    assert submit.path == "/minimax/h3-max/text-to-video"
    body = submit.body
    assert body["resolution"] == "1080P"
    assert body["duration"] == 7
    assert body["aspect_ratio"] == "16:9"
    assert body["prompt_expansion_mode"] == "balanced"
    assert body["enable_safety_checker"] is True
    assert "aspect_ratio" not in prepare("h3max", _h3v()).body
    # turbo 半价
    assert prepare("h3maxturbo", _h3v(duration=7, resolution="1080p")).usd == Decimal("0.56")


def test_h3_float_duration() -> None:
    import json

    req = _h3v()
    req.duration = json.loads("7.5")
    submit = prepare("h3max", req)
    assert submit.body["duration"] == 7.5
    assert submit.usd == Decimal("0.60")  # 0.08 × 7.5

    with pytest.raises(ValidationError, match="0.92~15"):
        prepare("h3max", _h3v(duration=0))
    with pytest.raises(ValidationError, match="0.92~15"):
        prepare("h3max", _h3v(duration=16))


def test_h3_i2v_first_and_end_frame() -> None:
    one = prepare("h3max", _h3v(images=[_PNG]))
    assert one.mode == "image_to_video"
    assert str(one.body["image_url"]).startswith("data:image/")
    assert "end_image_url" not in one.body
    two = prepare("h3maxturbo", _h3v(images=[_PNG, _PNG]))
    assert "end_image_url" in two.body
    # last_only:仅尾帧
    last = prepare("h3max", _h3v(images=[_PNG], params={"frame_mode": "last_only"}))
    assert "end_image_url" in last.body and "image_url" not in last.body


def test_h3_auto_routes_to_reference_with_av_or_many_images() -> None:
    vid = MediaRef(kind=MediaKind.VIDEO, url="https://example.com/a.mp4")
    aud = MediaRef(kind=MediaKind.AUDIO, url="https://example.com/a.mp3")
    assert prepare("h3max", _h3v(video_refs=[vid])).mode == "reference_to_video"
    assert prepare("h3max", _h3v(audio_refs=[aud])).mode == "reference_to_video"
    assert prepare("h3max", _h3v(images=[_PNG] * 3)).mode == "reference_to_video"
    sub = prepare("h3maxturbo", _h3v(images=[_PNG] * 4, video_refs=[vid]))
    assert sub.mode == "reference_to_video"
    assert len(sub.body["reference_image_urls"] if isinstance(sub.body["reference_image_urls"], list) else []) == 4
    assert sub.body["reference_video_urls"] == ["https://example.com/a.mp4"]


def test_h3_reference_mode_explicit_lock() -> None:
    with pytest.raises(ValidationError, match="至少 1 个参考素材"):
        prepare("h3max", _h3v(params={"task_mode": "reference_to_video"}))
    with pytest.raises(ValidationError, match="不能带参考素材"):
        prepare("h3max", _h3v(params={"task_mode": "text_to_video"}, images=[_PNG]))
    with pytest.raises(ValidationError, match="1~2 张"):
        prepare("h3max", _h3v(params={"task_mode": "image_to_video"}, images=[_PNG] * 3))


def test_h3_reference_caps() -> None:
    with pytest.raises(ValidationError, match="最多 9 张"):
        prepare("h3max", _h3v(images=[_PNG] * 10))
    vids = [MediaRef(kind=MediaKind.VIDEO, url="https://example.com/a.mp4")] * 4
    with pytest.raises(ValidationError, match="最多 3 段"):
        prepare("h3max", _h3v(video_refs=vids))
    auds = [MediaRef(kind=MediaKind.AUDIO, url="https://example.com/a.mp3")] * 4
    with pytest.raises(ValidationError, match="最多 3 段"):
        prepare("h3max", _h3v(audio_refs=auds))


def test_h3_frame_role_only_on_images() -> None:
    # flat 路径:视频/音频 ref 带帧位 role 直接报错(帧位只能是图片)
    vid = MediaRef(kind=MediaKind.VIDEO, url="https://example.com/a.mp4", role="first_frame")
    with pytest.raises(ValidationError, match="帧位只能是图片"):
        prepare("h3max", _h3v(images=[_PNG] * 3, video_refs=[vid]))
    # OC 路径同理
    ordered = [
        ContentItem(type=ContentItemType.VIDEO, media=vid),
        ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, data=_PNG)),
    ]
    with pytest.raises(ValidationError, match="帧位只能是图片"):
        prepare("h3max", _h3v(ordered=ordered))


def test_h3_frame_modes_in_reference() -> None:
    sub = prepare("h3max", _h3v(images=[_PNG] * 5, params={"frame_mode": "first_last"}))
    assert "image_url" in sub.body and "end_image_url" in sub.body
    refs = sub.body["reference_image_urls"]
    assert isinstance(refs, list) and len(refs) == 3

    sub = prepare("h3max", _h3v(images=[_PNG] * 3, params={"frame_mode": "first_middle_last"}, duration=6))
    assert "middle_image_url" in sub.body
    assert sub.body["middle_frame_time"] == 3.0
    # 3 张图全部被帧位消耗,不再有参考图列表
    assert "reference_image_urls" not in sub.body

    with pytest.raises(ValidationError, match="至少 3 张"):
        prepare(
            "h3max",
            _h3v(images=[_PNG] * 2, params={"task_mode": "reference_to_video", "frame_mode": "first_middle_last"}),
        )
    with pytest.raises(ValidationError, match="不支持关键帧位"):
        prepare("h3maxturbo", _h3v(images=[_PNG] * 3, params={"frame_mode": "first_last"}))
    with pytest.raises(ValidationError, match="不支持关键帧位"):
        prepare("h3maxturbo", _h3v(images=[_PNG] * 3, params={"frame_mode": "last_only"}))


def test_h3_middle_frame_constraints() -> None:
    base = _h3v(images=[_PNG] * 3, params={"frame_mode": "first_middle_last"}, duration=6)
    sub = prepare("h3max", base)
    assert sub.body["middle_frame_time"] == 3.0
    # 越界时刻
    bad = _h3v(images=[_PNG] * 3, params={"frame_mode": "first_middle_last", "middle_frame_time": 6}, duration=6)
    with pytest.raises(ValidationError, match="middle_frame_time"):
        prepare("h3max", bad)
    # 1080p 不支持中间帧
    bad2 = _h3v(images=[_PNG] * 3, resolution="1080p", params={"frame_mode": "first_middle_last"}, duration=6)
    with pytest.raises(ValidationError, match="1080p"):
        prepare("h3max", bad2)


def test_h3_ordered_content_maps_to_reference_conditions() -> None:
    ordered = [
        ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, data=_PNG), role="first_frame"),
        ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, url="https://e.com/b.png")),
        ContentItem(type=ContentItemType.VIDEO, media=MediaRef(kind=MediaKind.VIDEO, url="https://e.com/v.mp4")),
        ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, data=_PNG), role="last_frame"),
    ]
    sub = prepare("h3max", _h3v(ordered=ordered))
    assert sub.mode == "reference_to_video"
    conds = sub.body["reference_conditions"]
    assert isinstance(conds, list)
    assert [c["type"] for c in conds if isinstance(c, dict)] == ["image", "video"]
    # 帧位拆到独立字段,不进 conditions
    assert "image_url" in sub.body and "end_image_url" in sub.body
    assert sub.body["reference_conditions"] == [
        {"type": "image", "url": "https://e.com/b.png"},
        {"type": "video", "url": "https://e.com/v.mp4"},
    ]


def test_h3_ordered_text_merges_into_prompt() -> None:
    ordered = [
        ContentItem(type=ContentItemType.TEXT, text="一只猫在月球上奔跑"),
        ContentItem(type=ContentItemType.TEXT, text="电影感"),
        ContentItem(type=ContentItemType.IMAGE, media=MediaRef(kind=MediaKind.IMAGE, data=_PNG)),
    ]
    sub = prepare("h3max", _h3v(ordered=ordered))
    assert sub.body["prompt"] == "一只猫在月球上奔跑\n电影感"


def test_h3_ratio_adaptive_only_in_reference() -> None:
    with pytest.raises(ValidationError, match="adaptive"):
        prepare("h3max", _h3v(ratio="adaptive"))
    ok = prepare("h3max", _h3v(images=[_PNG] * 3, ratio="adaptive"))
    assert ok.body["aspect_ratio"] == "adaptive"


def test_h3_target_audio_constraints() -> None:
    ok = prepare("h3max", _h3v(params={"target_audio_url": "https://e.com/a.mp3"}))
    assert ok.body["target_audio_url"] == "https://e.com/a.mp3"
    # 参考模式与参考音频互斥
    with pytest.raises(ValidationError, match="不能与参考音频"):
        prepare(
            "h3max",
            _h3v(
                images=[_PNG] * 3,
                audio_refs=[MediaRef(kind=MediaKind.AUDIO, url="https://e.com/b.mp3")],
                params={"target_audio_url": "https://e.com/a.mp3"},
            ),
        )


def test_h3_generate_audio_false_rejected() -> None:
    req = _h3v()
    req.generate_audio = False
    with pytest.raises(ValidationError, match="恒含原生音频"):
        prepare("h3max", req)
    with pytest.raises(ValidationError, match="恒含原生音频"):
        prepare("h3maxturbo", req)


def test_h3_validation_rejects_bad_enums() -> None:
    with pytest.raises(ValidationError, match="prompt_expansion"):
        prepare("h3max", _h3v(params={"prompt_expansion": "max"}))
    with pytest.raises(ValidationError, match="frame_mode"):
        prepare("h3max", _h3v(params={"frame_mode": "nope"}))
    with pytest.raises(ValidationError, match="480p/768p/1080p"):
        prepare("h3max", _h3v(resolution="2k"))
    with pytest.raises(ValidationError, match="safety_checker"):
        prepare("h3max", _h3v(params={"safety_checker": "maybe"}))
    sub = prepare("h3max", _h3v(params={"safety_checker": False}))
    assert sub.body["enable_safety_checker"] is False


# ── H3 计费精确性(estimate / settle)──


def test_h3_estimate_and_settle_reference_tokens() -> None:
    from RH_ComfyUI.models.video.fal import H3MaxVideo, H3MaxTurboVideo

    model = H3MaxVideo()
    turbo = H3MaxTurboVideo()

    # t2v 基费:h3max 768p×5s = $0.40 → 280 分;turbo 768p×5s = $0.20 → 140 分
    assert model.estimate_cost(_h3v(resolution="768p")) == 280
    assert turbo.estimate_cost(_h3v(resolution="768p")) == 140

    # 5 图 + 1 视频参考:估算 6×1024=6144 token,超 2048 → $0.40 + $0.04096 = $0.44096 → 309 分
    vid = MediaRef(kind=MediaKind.VIDEO, url="https://example.com/a.mp4")
    req = _h3v(images=[_PNG] * 5, video_refs=[vid])
    assert model.estimate_cost(req) == 309
    # turbo 参考模式同基费,但 token 费率 $0.016/1K:$0.40 + $0.032768 = $0.432768 → 303 分
    assert turbo.estimate_cost(req) == 303

    # settle:上游实报 2048 token(未超 4096)→ 退超额差,回到 $0.40 → 280 分
    settled = model.settle_cost(req, {"reference_tokens": 2048})
    assert settled == 280
    # 实报 8192 → $0.40 + 4096/1000×0.02 = $0.48192 → 338 分
    settled = model.settle_cost(req, {"reference_tokens": 8192})
    assert settled == 338
    # usage 缺 token → 维持估算价
    settled = model.settle_cost(req, {})
    assert settled == 309


def test_h3_models_registered() -> None:
    from RH_ComfyUI.models.video.fal import H3MaxVideo, H3MaxTurboVideo

    names = {cls.node_def().name for cls in VIDEO_MODELS}
    assert "h3max" in names and "h3maxturbo" in names

    for cls, expected_backend_model in ((H3MaxVideo, "minimax/h3-max"), (H3MaxTurboVideo, "minimax/h3-max-turbo")):
        model = cls()
        node = model.node
        assert node.backend == "fal"
        assert node.backend_model == expected_backend_model
        assert set(node.inputs["task_mode"].values or []) == {
            "auto",
            "text_to_video",
            "image_to_video",
            "reference_to_video",
        }
        assert set(node.inputs["resolution"].values or []) == {"480p", "768p", "1080p"}
        bindings = model.channel_bindings()
        assert bindings and bindings[0].vendor_model == expected_backend_model
        # 多模态参考须过基类形态闸
        model.validate(_h3v(video_refs=[MediaRef(kind=MediaKind.VIDEO, url="https://e.com/a.mp4")]))

    # 关键帧位只有 h3max 有;turbo 参考端点无此字段
    H3MaxVideo().validate(_h3v(images=[_PNG] * 3, params={"frame_mode": "first_last"}))
    with pytest.raises(ValidationError):
        H3MaxTurboVideo().validate(_h3v(images=[_PNG] * 3, params={"frame_mode": "first_last"}))


def test_h3_turbo_allows_flat_reference_images() -> None:
    # turbo 参考模式:图都进 reference_image_urls,无关键帧位
    sub = prepare("h3maxturbo", _h3v(images=[_PNG] * 3))
    assert sub.mode == "reference_to_video"
    assert isinstance(sub.body["reference_image_urls"], list)
    assert len(sub.body["reference_image_urls"]) == 3
    assert "image_url" not in sub.body


def test_h3_run_uses_model_app_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)
    seen: list[str] = []

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None, json: object = None) -> _Resp:
            seen.append(url)
            return _Resp({"request_id": "req-h3"})

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            seen.append(url)
            if url.endswith("/status"):
                return _Resp({"status": "COMPLETED"})
            return _Resp({"video": {"url": "https://cdn.example/a.mp4"}, "reference_tokens": 5120})

    async def _download(url: str, **kwargs: object) -> bytes:
        return b"ok"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.download_with_network_retry", _download)
    submit = prepare("h3maxturbo", _h3v(images=[_PNG] * 3))
    output = asyncio.run(FalAPI().run(submit, model_name="h3maxturbo"))
    assert output.usage["request_id"] == "req-h3"
    # 上游实报 reference_tokens 进 usage,供 settle_cost 精确补差
    assert output.usage["reference_tokens"] == 5120
    assert seen[0] == f"{DEFAULT_QUEUE_ENDPOINT}/minimax/h3-max-turbo/reference-to-video"
    assert seen[1] == f"{DEFAULT_QUEUE_ENDPOINT}/minimax/h3-max-turbo/requests/req-h3/status"
    assert seen[2] == f"{DEFAULT_QUEUE_ENDPOINT}/minimax/h3-max-turbo/requests/req-h3"


def test_h3_resume_polls_model_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
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
    output = asyncio.run(FalAPI().resume("req-9", model_name="h3max"))
    assert output.data == b"resumed"
    assert calls[0] == f"{DEFAULT_QUEUE_ENDPOINT}/minimax/h3-max/requests/req-9/status"


# ═══════════════════════════════════════════════════════════
#  共享模型挂 fal 通道:minimax_h3 / seedance2·2.5 / banana / gpt-image-2
# ═══════════════════════════════════════════════════════════

from RH_ComfyUI.utils.backends.fal.parse import image_of  # noqa: E402


def _img(
    prompt: str = "一只猫坐在窗边",
    *,
    images: list[bytes] | None = None,
    ratio: str | None = None,
    params: dict[str, object] | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        task_type=TaskType.IMAGE,
        prompt=prompt,
        images=list(images or []),
        ratio=ratio,
        params=dict(params or {}),
    )


# ── minimax_h3 ──


def test_mmh3_t2v_and_resolution_map() -> None:
    sub = prepare("minimax_h3", _h3v(duration=8, resolution="2k", ratio="21:9"))
    assert sub.mode == "text_to_video"
    assert sub.app == "minimax/h3"
    assert sub.path == "/minimax/h3/text-to-video"
    assert sub.body["resolution"] == "2K"
    assert sub.body["duration"] == 8
    assert sub.body["aspect_ratio"] == "21:9"
    sub = prepare("minimax_h3", _h3v(resolution="768p"))
    assert sub.body["resolution"] == "768P"


def test_mmh3_i2v_frame_roles() -> None:
    one = prepare("minimax_h3", _h3v(images=[_PNG]))
    assert one.mode == "image_to_video"
    assert "image_url" in one.body and "end_image_url" not in one.body
    two = prepare("minimax_h3", _h3v(images=[_PNG, _PNG]))
    assert "end_image_url" in two.body
    last = prepare("minimax_h3", _h3v(images=[_PNG], params={"frame_mode": "last_frame"}))
    assert "end_image_url" in last.body and "image_url" not in last.body
    fl = prepare("minimax_h3", _h3v(images=[_PNG, _PNG], params={"task_mode": "first_last"}))
    assert "image_url" in fl.body and "end_image_url" in fl.body


def test_mmh3_reference_and_caps() -> None:
    sub = prepare("minimax_h3", _h3v(images=[_PNG] * 3))
    assert sub.mode == "reference_to_video"
    assert sub.path == "/minimax/h3/reference-to-video"
    assert isinstance(sub.body["reference_image_urls"], list)
    assert len(sub.body["reference_image_urls"]) == 3
    ref = prepare("minimax_h3", _h3v(images=[_PNG], params={"frame_mode": "reference"}))
    assert ref.mode == "reference_to_video"
    with pytest.raises(ValidationError, match="最多 9 张"):
        prepare("minimax_h3", _h3v(images=[_PNG] * 10))
    # 参考模式带帧位:fal 无此字段
    with pytest.raises(ValidationError, match="不支持首/尾帧位"):
        prepare("minimax_h3", _h3v(images=[_PNG] * 3, params={"frame_mode": "first_last"}))


def test_mmh3_rejects_unsupported() -> None:
    with pytest.raises(ValidationError, match="时长"):
        prepare("minimax_h3", _h3v(duration=4))
    with pytest.raises(ValidationError, match="watermark"):
        prepare("minimax_h3", _h3v(params={"watermark": True}))
    req = _h3v()
    req.seed = 7
    with pytest.raises(ValidationError, match="seed"):
        prepare("minimax_h3", req)
    with pytest.raises(ValidationError, match="adaptive"):
        prepare("minimax_h3", _h3v(ratio="adaptive"))


# ── seedance2 / seedance2.5 ──


def test_seedance2_t2v_and_i2v() -> None:
    sub = prepare("seedance2", _h3v(duration=6, ratio="16:9", resolution="1080p"))
    assert sub.mode == "text_to_video"
    assert sub.app == "bytedance/seedance-2.0"
    assert sub.path == "/bytedance/seedance-2.0/text-to-video"
    assert sub.body["duration"] == "6"  # fal 枚举是字符串
    assert sub.body["aspect_ratio"] == "16:9"
    assert sub.body["generate_audio"] is True
    two = prepare("seedance2", _h3v(images=[_PNG, _PNG], ratio="adaptive"))
    assert two.mode == "image_to_video"
    assert two.body["aspect_ratio"] == "auto"
    assert "image_url" in two.body and "end_image_url" in two.body


def test_seedance2_reference_and_rejects() -> None:
    vid = MediaRef(kind=MediaKind.VIDEO, url="https://e.com/a.mp4")
    sub = prepare("seedance2", _h3v(video_refs=[vid]))
    assert sub.mode == "reference_to_video"
    assert sub.path == "/bytedance/seedance-2.0/reference-to-video"
    assert sub.body["video_urls"] == ["https://e.com/a.mp4"]
    with pytest.raises(ValidationError, match="自动"):
        prepare("seedance2", _h3v(duration=-1))
    with pytest.raises(ValidationError, match="watermark"):
        prepare("seedance2", _h3v(params={"watermark": True}))
    with pytest.raises(ValidationError, match="adaptive"):
        prepare("seedance2", _h3v(images=[_PNG], ratio="16:9"))


def test_seedance25_edit_extend_and_draft() -> None:
    vid = MediaRef(kind=MediaKind.VIDEO, url="https://e.com/a.mp4")
    sub = prepare("seedance2.5", _h3v(video_refs=[vid], params={"task_mode": "edit"}))
    assert sub.mode == "reference_to_video"
    assert sub.body["task"] == "editing"
    sub = prepare("seedance2.5", _h3v(video_refs=[vid], params={"omni_reference_task_type": "extend"}))
    assert sub.body["task"] == "extension"
    with pytest.raises(ValidationError, match="参考视频"):
        prepare("seedance2.5", _h3v(params={"task_mode": "edit"}))
    # duration=-1 → "auto"
    sub = prepare("seedance2.5", _h3v(duration=-1))
    assert sub.body["duration"] == "auto"
    # draft 直通且锁 480p
    sub = prepare("seedance2.5", _h3v(params={"draft": True}, resolution="1080p"))
    assert sub.body["draft"] is True
    assert sub.body["resolution"] == "480p"
    with pytest.raises(ValidationError, match="draft_task_id"):
        prepare("seedance2.5", _h3v(params={"draft_task_id": "x"}))
    with pytest.raises(ValidationError, match="output_format"):
        prepare("seedance2.5", _h3v(params={"output_format": "mov"}))


def test_seedance25_ref_caps() -> None:
    imgs = [_PNG] * 31
    with pytest.raises(ValidationError, match="最多 30 张"):
        prepare("seedance2.5", _h3v(images=imgs))
    vids = [MediaRef(kind=MediaKind.VIDEO, url="https://e.com/a.mp4")] * 11
    with pytest.raises(ValidationError, match="最多 10 段"):
        prepare("seedance2.5", _h3v(video_refs=vids))


# ── banana / gpt-image-2 ──


def test_banana1_t2i_and_edit() -> None:
    sub = prepare("banana1", _img(ratio="4:3"))
    assert sub.mode == "text_to_image"
    assert sub.output == "image"
    assert sub.app == "fal-ai/nano-banana"
    assert sub.path == "/fal-ai/nano-banana"
    assert sub.body["aspect_ratio"] == "4:3"
    assert "resolution" not in sub.body  # 一代无尺寸档
    edit = prepare("banana1", _img(images=[_PNG, _PNG]))
    assert edit.mode == "edit"
    assert edit.path == "/fal-ai/nano-banana/edit"
    assert isinstance(edit.body["image_urls"], list) and len(edit.body["image_urls"]) == 2


def test_banana2_resolution_and_ratio_fold() -> None:
    sub = prepare("banana2", _img(params={"image_size": "512", "ratio": "8:5"}))
    assert sub.path == "/fal-ai/nano-banana-2"
    assert sub.body["resolution"] == "0.5K"
    assert sub.body["aspect_ratio"] == "3:2"  # 产品面 8:5 折成上游白名单 3:2
    pro = prepare("banana_pro", _img(images=[_PNG], params={"image_size": "4K"}))
    assert pro.path == "/fal-ai/nano-banana-pro/edit"
    assert pro.body["resolution"] == "4K"
    lite = prepare("banana2.lite", _img(params={"image_size": "1K", "thinking_level": "high"}))
    assert lite.app == "google/nano-banana-2-lite"
    assert lite.body["thinking_level"] == "high"
    with pytest.raises(ValidationError, match="image_size"):
        prepare("banana2.lite", _img(params={"image_size": "4K"}))


def test_gpt_image2_size_quality_format() -> None:
    sub = prepare("gpt-image-2", _img())
    assert sub.app == "openai/gpt-image-2"
    assert sub.path == "/openai/gpt-image-2"
    assert sub.body["image_size"] == {"width": 2048, "height": 2048}  # 默认 2K 方图
    auto = prepare("gpt-image-2", _img(params={"image_size": "1K"}))
    assert auto.body["image_size"] == "auto"
    wide = prepare("gpt-image-2", _img(params={"ratio": "16:9", "image_size": "4K"}))
    dims = wide.body["image_size"]
    assert isinstance(dims, dict)
    w, h = dims["width"], dims["height"]
    assert isinstance(w, int) and isinstance(h, int)
    assert w <= 3840 and w % 16 == 0 and h % 16 == 0
    edit = prepare("gpt-image-2", _img(images=[_PNG]))
    assert edit.path == "/openai/gpt-image-2/edit"
    assert isinstance(edit.body["image_urls"], list)


def test_gpt_image2_rejects_fal_gaps() -> None:
    with pytest.raises(ValidationError, match="quality"):
        prepare("gpt-image-2", _img(params={"quality": "xhigh"}))
    with pytest.raises(ValidationError, match="output_format"):
        prepare("gpt-image-2", _img(params={"output_format": "gif"}))
    with pytest.raises(ValidationError, match="transparent"):
        prepare("gpt-image-2", _img(params={"background": "transparent", "output_format": "jpeg"}))


def test_image_of_parses_result() -> None:
    url, mime = image_of({"images": [{"url": "https://cdn.example/a.png", "content_type": "image/png"}]})
    assert url == "https://cdn.example/a.png" and mime == "image/png"
    with pytest.raises(ChannelError):
        image_of({"video": {"url": "x"}})


def test_fal_image_run_returns_image(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_cfg(monkeypatch)

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        async def post(self, url: str, headers: dict[str, str] | None = None, json: object = None) -> _Resp:
            return _Resp({"request_id": "req-img"})

        async def get(self, url: str, headers: dict[str, str] | None = None) -> _Resp:
            if url.endswith("/status"):
                return _Resp({"status": "COMPLETED"})
            return _Resp({"images": [{"url": "https://cdn.example/a.webp", "content_type": "image/webp"}]})

    async def _download(url: str, **kwargs: object) -> bytes:
        return b"img"

    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.RetryingAsyncClient", _Client)
    monkeypatch.setattr("RH_ComfyUI.utils.backends.fal.api.download_with_network_retry", _download)
    submit = prepare("banana_pro", _img())
    output = asyncio.run(FalAPI().run(submit, model_name="banana_pro"))
    assert output.output_type == "image"
    assert output.mime_type == "image/webp"
    assert output.data == b"img"


def test_channel_maps_prepare_error_to_capability() -> None:
    # fal 表达不了的参数(watermark)→ CAPABILITY + BEFORE_SEND,换源不记健康
    ch = FalChannel("minimax_h3")
    req = _h3v(params={"watermark": True})
    with pytest.raises(ChannelError) as exc:
        asyncio.run(ch.invoke(request=req))
    from RH_ComfyUI.core.channels.failure import Phase, FailureKind

    assert exc.value.kind == FailureKind.CAPABILITY
    assert exc.value.phase == Phase.BEFORE_SEND


def test_shared_model_bindings_include_fal() -> None:
    from RH_ComfyUI.models.image.defs import Banana1Def, BananaProDef, GptImage2Def
    from RH_ComfyUI.models.video.defs import MiniMaxH3Def, Seedance2Def, Seedance25Def

    for cls in (MiniMaxH3Def, Seedance2Def, Seedance25Def, Banana1Def, BananaProDef, GptImage2Def):
        bindings = cls().channel_bindings()
        fal = [b for b in bindings if isinstance(b.channel, FalChannel)]
        assert fal, f"{cls.__name__} 缺少 fal 绑定"
        assert fal[0].vendor_model
    # 官方通道仍在 fal 之前
    sd = Seedance2Def().channel_bindings()
    assert not isinstance(sd[0].channel, FalChannel)
    assert isinstance(sd[-1].channel, FalChannel)
