"""run() 级集成回归:严格创建模式下的换源 / 健康记账 / vendor 绑定覆盖

覆盖生产事故根因:strict_create_once 下 create 503 原实现既换源也不计熔断。
"""

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any, Optional, Sequence

import httpx
import pytest

from RH_ComfyUI.core.base.errors import ChannelError, AllChannelsFailedError
from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.types import PortSpec, PortType, NodeOutput
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.base.generation import AIGCGenerationBase
from RH_ComfyUI.core.channels.channel import ChannelBinding, ProviderChannel
from RH_ComfyUI.core.channels.failure import Phase, Stage, FailureKind
from RH_ComfyUI.core.routing.balancer import LoadBalancer, BalancerConfig
from RH_ComfyUI.utils.backends.http_retry import strict_create_once_scope

# sys.path 上另有顶层 dispatch 包,直接 import 路径会被遮蔽;用 importlib 定位真模块
active_tasks = importlib.import_module("RH_ComfyUI.core.dispatch.active_tasks")


class _FakeChannel(ProviderChannel):
    """可编程假通道:errors 队列逐个抛,耗尽后返回成功;记录调用次数。"""

    def __init__(
        self,
        name: str,
        errors: Optional[list[ChannelError]] = None,
        group: str | None = None,
        succeed_after: int = 0,
    ) -> None:
        self.name = name
        self.errors = list(errors or [])
        self._group = group
        self.calls = 0
        self._succeed_after = succeed_after

    def health_group(self) -> str:
        return self._group if self._group is not None else self.name

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs: Any) -> NodeOutput:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        if self.calls <= self._succeed_after:
            raise ChannelError(
                "transient",
                kind=FailureKind.RATE_LIMITED,
                phase=Phase.REJECTED,
                channel=self.name,
            )
        return NodeOutput(output_type="image", data=f"ok-{self.name}".encode())


class _FakeRegistry:
    """极简 ActiveGeneration 替身:只为让 _attempt_phase 读到 vendor_task_id。"""

    def __init__(self) -> None:
        self.ag = SimpleNamespace(vendor_task_id=None, cancel_remote=None)

    def current(self) -> Any:
        return self.ag


class _FailoverModel(AIGCGenerationBase):
    modality = TaskType.IMAGE
    card = ModelCard(description="x")

    def __init__(self, bindings: list[ChannelBinding]) -> None:
        self.name = "failover_model"
        self.display_name = "failover_model"
        self._bindings = bindings
        self._lb = LoadBalancer(BalancerConfig(mode="round_robin"))
        # 排队预算收紧:测试里 transient 退避走极小值
        self.transient_retry_delay = 0.001
        self.transient_retry_max_delay = 0.005
        self.transient_retry_max_wait = 1.0

    def input_schema(self) -> dict:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return self._bindings

    def balancer(self) -> LoadBalancer:
        return self._lb

    async def execute_on_channel(
        self,
        request: GenerationRequest,
        binding: ChannelBinding,
        *,
        on_progress: Optional[Any] = None,
    ) -> NodeOutput:
        return await binding.channel.invoke(request=request, on_progress=on_progress)


def _model(*channels: _FakeChannel) -> _FailoverModel:
    return _FailoverModel([ChannelBinding(channel=c) for c in channels])


def _request() -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")


def _http_err(status: int, stage: Stage, channel: str = "") -> ChannelError:
    return ChannelError.from_http(status, stage=stage, channel=channel, message=f"HTTP {status}")


def test_strict_create_503_fails_over_and_records_group_health():
    """事故回归:strict + create 503(REJECTED)→ 换源成功,group 记账。"""
    a = _FakeChannel("gw-a", errors=[_http_err(503, "create")], group="host-a")
    b = _FakeChannel("gw-b", group="host-b")
    model = _model(a, b)
    with strict_create_once_scope(True):
        out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-gw-b"
    assert a.calls == 1 and b.calls == 1
    snap = model.balancer().health_snapshot()
    assert snap["group:host-a"]["failure_count"] == 1


def test_strict_create_500_ambiguous_raises_no_failover():
    """strict + AMBIGUOUS(500 on create)→ 原样抛,不切通道,健康仍记。"""
    a = _FakeChannel("gw-a", errors=[_http_err(500, "create")], group="g")
    b = _FakeChannel("gw-b", group="g2")
    model = _model(a, b)
    with strict_create_once_scope(True):
        with pytest.raises(ChannelError) as ei:
            asyncio.run(model.run(_request()))
    assert ei.value.http_status == 500
    assert b.calls == 0
    snap = model.balancer().health_snapshot()
    assert snap["group:g"]["failure_count"] == 1


def test_strict_transport_connect_error_fails_over():
    """strict + ConnectError(BEFORE_SEND,未发出)→ 换源成功。"""
    req = httpx.Request("POST", "http://x.invalid/")
    err = ChannelError.from_transport(httpx.ConnectError("conn refused", request=req), stage="create", channel="gw-a")
    a = _FakeChannel("gw-a", errors=[err])
    b = _FakeChannel("gw-b")
    model = _model(a, b)
    with strict_create_once_scope(True):
        out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-gw-b"
    assert a.calls == 1


def test_strict_transport_read_timeout_raises():
    """strict + ReadTimeout(AMBIGUOUS,可能已受理)→ 原样抛不换源。"""
    req = httpx.Request("POST", "http://x.invalid/")
    err = ChannelError.from_transport(httpx.ReadTimeout("slow", request=req), stage="create", channel="gw-a")
    a = _FakeChannel("gw-a", errors=[err])
    b = _FakeChannel("gw-b")
    model = _model(a, b)
    with strict_create_once_scope(True):
        with pytest.raises(ChannelError):
            asyncio.run(model.run(_request()))
    assert b.calls == 0


def test_strict_legacy_retryable_error_raises():
    """strict + 旧式 ChannelError(retryable=True,无 phase)→ raise(旧行为保持)。"""
    a = _FakeChannel("a", errors=[ChannelError("legacy", retryable=True, channel="a")])
    b = _FakeChannel("b")
    model = _model(a, b)
    with strict_create_once_scope(True):
        with pytest.raises(ChannelError):
            asyncio.run(model.run(_request()))
    assert b.calls == 0


def test_nonstrict_ambiguous_fails_over():
    """非 strict + AMBIGUOUS(500)→ 换源(与旧行为一致)。"""
    a = _FakeChannel("a", errors=[_http_err(500, "create")])
    b = _FakeChannel("b")
    model = _model(a, b)
    out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-b"


def test_vendor_task_bound_then_5xx_no_failover(monkeypatch):
    """本次尝试已绑定 vendor_task_id → phase 覆盖 ACCEPTED → 两种模式都不换源。"""
    registry = _FakeRegistry()
    monkeypatch.setattr(active_tasks, "get_active_task_registry", lambda: registry)

    class _BindThenFail(_FakeChannel):
        async def invoke(self, **kwargs: Any) -> NodeOutput:
            self.calls += 1
            registry.ag.vendor_task_id = "task-42"
            raise _http_err(502, "poll", channel=self.name)

    for strict in (False, True):
        a = _BindThenFail("a")
        b = _FakeChannel("b")
        model = _model(a, b)
        if strict:
            with strict_create_once_scope(True), pytest.raises(ChannelError):
                asyncio.run(model.run(_request()))
        else:
            with pytest.raises(ChannelError):
                asyncio.run(model.run(_request()))
        assert a.calls == 1 and b.calls == 0


def test_transient_503_with_alternative_fails_over_immediately(monkeypatch):
    """transient(503) 有备选 → 立即换源,不在原通道排队(增强点)。"""
    slept: list[float] = []

    async def _fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    a = _FakeChannel("a", errors=[_http_err(503, "create")])
    b = _FakeChannel("b")
    model = _model(a, b)
    out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-b"
    assert slept == []  # 一次退避都不睡


def test_transient_503_single_channel_retries_in_place(monkeypatch):
    """单通道 transient 503 → 原通道指数退避后成功(旧排队语义保持)。"""
    slept: list[float] = []

    async def _fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    a = _FakeChannel(
        "a",
        errors=[_http_err(503, "create"), _http_err(503, "create")],
    )
    model = _model(a)
    out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-a"
    assert a.calls == 3
    assert len(slept) == 2 and slept[0] < slept[1]  # 指数增长


def test_strict_transient_503_single_channel_retries_in_place(monkeypatch):
    """strict + REJECTED 503 + 无备选 → 仍允许原地退避(strict 只禁换源,不禁确定未建单的重试)。"""
    slept: list[float] = []

    async def _fake_sleep(d: float) -> None:
        slept.append(d)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    a = _FakeChannel("a", errors=[_http_err(503, "create")])
    model = _model(a)
    with strict_create_once_scope(True):
        out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-a"
    assert a.calls == 2
    assert len(slept) == 1


def test_invalid_request_no_failover_no_health():
    """参数类(400 INVALID_REQUEST)→ 不换源、不计健康。"""
    a = _FakeChannel("a", errors=[_http_err(400, "create")])
    b = _FakeChannel("b")
    model = _model(a, b)
    with pytest.raises(ChannelError) as ei:
        asyncio.run(model.run(_request()))
    assert ei.value.http_status == 400
    assert b.calls == 0
    assert model.balancer().health_snapshot() == {}


def test_accepted_task_failed_nonstrict_fails_over_strict_raises():
    """上游受理后报失败(TASK_FAILED+ACCEPTED):非 strict 换源,strict 抛。"""
    err = ChannelError(
        "task failed upstream",
        kind=FailureKind.TASK_FAILED,
        phase=Phase.ACCEPTED,
        channel="a",
    )
    a1 = _FakeChannel("a", errors=[err])
    b1 = _FakeChannel("b")
    model = _model(a1, b1)
    out = asyncio.run(model.run(_request()))
    assert out.data == b"ok-b"

    a2 = _FakeChannel(
        "a",
        errors=[
            ChannelError(
                "task failed upstream",
                kind=FailureKind.TASK_FAILED,
                phase=Phase.ACCEPTED,
                channel="a",
            )
        ],
    )
    b2 = _FakeChannel("b")
    model2 = _model(a2, b2)
    with strict_create_once_scope(True), pytest.raises(ChannelError):
        asyncio.run(model2.run(_request()))
    assert b2.calls == 0


def test_exhausted_raises_all_channels_failed():
    a = _FakeChannel("a", errors=[_http_err(500, "create")])
    b = _FakeChannel("b", errors=[_http_err(500, "create")])
    model = _model(a, b)
    with pytest.raises(AllChannelsFailedError):
        asyncio.run(model.run(_request()))


# ── 供应商级轮询容错(D 项):5xx 容忍续轮 / 窗口耗尽原错非 retryable ──


class _FlakyPollProvider:
    """最小 SeedanceProvider 替身:get() 先抛 N 个错误再成功。"""

    @staticmethod
    def build(errors: Sequence[BaseException]):
        from RH_ComfyUI.utils.backends.seedance.provider import (
            NormalizedTask,
            NormalizedStatus,
            SeedanceProvider,
        )

        errs = list(errors)

        class _P(SeedanceProvider):
            name = "flaky"
            DEFAULT_BASE_URL = "http://flaky.local"
            calls = 0

            async def render_create(self, spec, *, model):
                return "POST", "http://flaky.local", {}, {}

            def parse_create(self, resp_json):
                return "tid-1"

            async def get(self, task_id):
                type(self).calls += 1
                if errs:
                    raise errs.pop(0)
                return NormalizedTask(id=task_id, status=NormalizedStatus.SUCCEEDED, video_url="http://f/v.mp4")

        return _P(api_key="k", base_url="http://flaky.local")


def test_provider_poll_tolerates_5xx_then_succeeds(monkeypatch):
    """poll 阶段上游 5xx:容忍窗内退避续轮,直至成功。"""
    from RH_ComfyUI.utils.backends.seedance.provider import NormalizedStatus, SeedanceProviderError

    async def _nosleep(d: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    errors = [
        SeedanceProviderError("upstream 503", code="HTTP_ERROR", http_status=503, provider="flaky"),
        SeedanceProviderError("upstream 502", code="HTTP_ERROR", http_status=502, provider="flaky"),
    ]
    provider = _FlakyPollProvider.build(errors)
    task = asyncio.run(provider.poll_until_done("tid-1", interval=0.001))
    assert task.status is NormalizedStatus.SUCCEEDED


def test_provider_poll_business_error_not_tolerated(monkeypatch):
    """poll 阶段无 http_status 的语义错误(业务拒答)不容忍,立即抛不白等窗口。"""
    from RH_ComfyUI.core.channels.failure import FailureKind
    from RH_ComfyUI.utils.backends.seedance.provider import SeedanceProviderError

    async def _nosleep(d: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    errors = [
        SeedanceProviderError(
            "DashScope 错误 InvalidParameter: bad",
            code="InvalidParameter",
            kind=FailureKind.INVALID_REQUEST,
            provider="flaky",
        ),
    ]
    provider = _FlakyPollProvider.build(errors)
    with pytest.raises(SeedanceProviderError) as ei:
        asyncio.run(provider.poll_until_done("tid-1", interval=0.001))
    assert ei.value.code == "InvalidParameter"
    assert type(provider).calls == 1  # 一次即抛,未进容忍窗重试


def test_provider_poll_network_error_exhaust_non_retryable(monkeypatch):
    """轮询传输错误超容忍窗 → POLL_NETWORK_ERROR,phase=ACCEPTED 不可换源。"""
    from RH_ComfyUI.utils.backends.seedance.provider import SeedanceProviderError

    async def _nosleep(d: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _nosleep)
    req = httpx.Request("GET", "http://flaky.local/task")
    provider = _FlakyPollProvider.build([httpx.ReadError("conn reset", request=req)] * 4)
    # 零窗:第一次容忍(建立窗口起点),第二次立即耗尽抛 POLL_NETWORK_ERROR
    provider.poll_error_window_s = 0.0
    with pytest.raises(SeedanceProviderError) as ei:
        asyncio.run(provider.poll_until_done("tid-1", interval=0.001))
    assert ei.value.code == "POLL_NETWORK_ERROR"
    assert ei.value.retryable is False
    assert ei.value.phase is Phase.ACCEPTED
