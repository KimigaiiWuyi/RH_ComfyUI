"""dispatcher + billing:校验失败不扣费 / 执行失败退款且幂等 / 成功 commit"""

import asyncio
from typing import Any, Optional

import pytest

from RH_ComfyUI.core.base.errors import ChannelError, ValidationError, BillingDeniedError
from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.types import PortSpec, PortType, NodeOutput
from RH_ComfyUI.core.billing.policy import (
    BillingPolicy,
    BillingContext,
    BillingReservation,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.base.generation import AIGCGenerationBase
from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding
from RH_ComfyUI.core.dispatch.context import DispatchContext
from RH_ComfyUI.core.routing.registry import model_registry
from RH_ComfyUI.core.dispatch.dispatcher import dispatch


class FakePolicy(BillingPolicy):
    def __init__(self, balance: int = 100) -> None:
        self.balance = balance
        self.reserved = 0
        self.refunds = 0
        self.commits = 0

    async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
        if self.balance < cost:
            raise BillingDeniedError("积分不足")
        self.balance -= cost
        self.reserved += 1
        return BillingReservation(cost=cost, context=ctx)

    async def refund(self, reservation: BillingReservation) -> None:
        if reservation.refunded:
            return
        self.balance += reservation.cost
        self.refunds += 1
        reservation.refunded = True

    async def commit(self, reservation: BillingReservation) -> None:
        self.commits += 1
        reservation.committed = True


class FakeModel(AIGCGenerationBase):
    modality = TaskType.IMAGE
    card = ModelCard(description="fake")
    name = "fake_dispatch_model"
    display_name = "Fake"
    point_cost = 5

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def input_schema(self) -> dict[str, PortSpec]:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(channel=LocalChannel("local"))]

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        if self.fail:
            raise ChannelError("上游挂了", retryable=False)
        return NodeOutput(output_type="image", data=b"png", mime_type="image/png")


def _ctx(policy: FakePolicy) -> DispatchContext:
    return DispatchContext(
        billing=BillingContext(user_id="u1", bot_id="test", entry_point="command"),
        policy=policy,
    )


@pytest.fixture
def registered_model():
    model = FakeModel()
    model_registry.register(model)
    yield model
    model_registry.unregister(model.name)


def test_success_commits(registered_model, monkeypatch):
    _mute_recording(monkeypatch)
    policy = FakePolicy()
    result = asyncio.run(
        dispatch(
            GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model="fake_dispatch_model"),
            _ctx(policy),
        )
    )
    assert result.data == b"png"
    assert policy.commits == 1 and policy.refunds == 0
    assert policy.balance == 95


def test_validation_error_no_billing(registered_model, monkeypatch):
    _mute_recording(monkeypatch)
    policy = FakePolicy()
    with pytest.raises(ValidationError):
        asyncio.run(
            dispatch(
                GenerationRequest(task_type=TaskType.IMAGE, prompt="", model="fake_dispatch_model"),
                _ctx(policy),
            )
        )
    assert policy.reserved == 0 and policy.balance == 100  # 校验先于扣费


def test_failure_refunds(monkeypatch):
    _mute_recording(monkeypatch)
    model = FakeModel(fail=True)
    model_registry.register(model)
    try:
        policy = FakePolicy()
        with pytest.raises(Exception):
            asyncio.run(
                dispatch(
                    GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model="fake_dispatch_model"),
                    _ctx(policy),
                )
            )
        assert policy.refunds == 1 and policy.balance == 100
    finally:
        model_registry.unregister(model.name)


class _Interrupt(BaseException):
    """模拟 DryRunInterrupt 这类继承 BaseException 的中断信号"""


class _InterruptModel(FakeModel):
    name = "fake_interrupt_model"

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        raise _Interrupt("dry-run")


def test_base_exception_still_refunds(monkeypatch):
    # 回归:DryRunInterrupt(BaseException)曾绕过 except Exception,
    # 预扣的积分一去不回;dispatcher 现按 BaseException 兜底退款后原样抛出
    recorded: list[str] = []
    _capture_recording(monkeypatch, recorded)
    model = _InterruptModel()
    model_registry.register(model)
    try:
        policy = FakePolicy()
        with pytest.raises(_Interrupt):
            asyncio.run(
                dispatch(
                    GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model=model.name),
                    _ctx(policy),
                )
            )
        assert policy.refunds == 1 and policy.balance == 100
        assert recorded == ["failed"]
    finally:
        model_registry.unregister(model.name)


class _CancelModel(FakeModel):
    name = "fake_cancel_model"

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        raise asyncio.CancelledError()


def test_cancellation_refunds_and_records_cancelled(monkeypatch):
    recorded: list[str] = []
    _capture_recording(monkeypatch, recorded)
    model = _CancelModel()
    model_registry.register(model)
    try:
        policy = FakePolicy()
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                dispatch(
                    GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model=model.name),
                    _ctx(policy),
                )
            )
        assert policy.refunds == 1 and policy.balance == 100
        assert recorded == ["cancelled"]
    finally:
        model_registry.unregister(model.name)


class _TieredCostModel(FakeModel):
    """动态计费:按参数分档(模拟 1080p 比 480p 贵)"""

    name = "fake_tiered_model"
    point_cost = 5

    def input_schema(self) -> dict[str, PortSpec]:
        return {
            "prompt": PortSpec(type=PortType.TEXT, required=True),
            "resolution": PortSpec(type=PortType.TEXT),
        }

    def estimate_cost(self, request: GenerationRequest) -> int:
        return 12 if request.resolution == "1080p" else self.point_cost


def test_estimate_cost_drives_reserve_and_result(monkeypatch):
    # 动态计费钩子:reserve 金额与 result.cost_points 都以 estimate_cost 为准
    _mute_recording(monkeypatch)
    model = _TieredCostModel()
    model_registry.register(model)
    try:
        policy = FakePolicy()
        result = asyncio.run(
            dispatch(
                GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model=model.name, resolution="1080p"),
                _ctx(policy),
            )
        )
        assert result.cost_points == 12
        assert policy.balance == 88  # 扣的是动态金额,不是静态 point_cost=5

        result2 = asyncio.run(
            dispatch(
                GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model=model.name),
                _ctx(policy),
            )
        )
        assert result2.cost_points == 5  # 默认档 = 静态 point_cost
    finally:
        model_registry.unregister(model.name)


class _SlowModel(FakeModel):
    name = "fake_slow_model"

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        await asyncio.sleep(0.5)
        return NodeOutput(output_type="image", data=b"png")


def test_dispatch_timeout_refunds_and_records_failed(monkeypatch):
    # 超时预算:卡死的上游不能无限占并发闸;超时按失败处理(落统计 + 退款)
    import importlib

    recorded: list[str] = []
    _capture_recording(monkeypatch, recorded)
    disp = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")
    monkeypatch.setattr(disp, "_resolve_timeout", lambda: 0.05)

    model = _SlowModel()
    model_registry.register(model)
    try:
        policy = FakePolicy()
        with pytest.raises(Exception) as ei:
            asyncio.run(
                dispatch(
                    GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model=model.name),
                    _ctx(policy),
                )
            )
        assert "超时" in str(getattr(ei.value, "user_message", "")) or "超" in str(ei.value)
        assert policy.refunds == 1 and policy.balance == 100
        assert recorded == ["failed"]  # 超时是失败,不能记成 cancelled
    finally:
        model_registry.unregister(model.name)


def test_dispatch_timeout_zero_means_unlimited(monkeypatch):
    import importlib

    _mute_recording(monkeypatch)
    disp = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")
    monkeypatch.setattr(disp, "_resolve_timeout", lambda: 0.0)

    model = _SlowModel()
    model_registry.register(model)
    try:
        policy = FakePolicy()
        result = asyncio.run(
            dispatch(
                GenerationRequest(task_type=TaskType.IMAGE, prompt="cat", model=model.name),
                _ctx(policy),
            )
        )
        assert result.data == b"png" and policy.commits == 1
    finally:
        model_registry.unregister(model.name)


class _MutatingModel(FakeModel):
    name = "fake_mutating_model"

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        request.prompt = "normalized prompt"
        request.params["quality"] = "mutated"
        return NodeOutput(output_type="image", data=b"png", mime_type="image/png")


def test_dispatch_records_request_before_model_mutates_it(monkeypatch):
    import importlib

    captured: dict[str, Any] = {}
    disp = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")

    async def _begin(**kwargs):
        return 1

    async def _capture(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(disp, "begin_dispatch", _begin)
    monkeypatch.setattr(disp, "record_dispatch", _capture)
    model = _MutatingModel()
    model_registry.register(model)
    try:
        request = GenerationRequest(
            task_type=TaskType.IMAGE,
            prompt="original prompt",
            model=model.name,
            params={"quality": "original", "image_base64": "AQID"},
        )
        asyncio.run(dispatch(request, _ctx(FakePolicy())))

        assert request.prompt == "normalized prompt"
        assert captured["request_body"]["prompt"] == "original prompt"
        assert captured["request_body"]["params"]["quality"] == "original"
        assert captured["request_body"]["params"]["image_base64"].startswith("<base64://")
        assert captured.get("record_id") == 1
    finally:
        model_registry.unregister(model.name)


def _mute_recording(monkeypatch):
    """统计落库依赖真实数据库,单测中静音"""
    import importlib

    # core/__init__ 中 `from .dispatch import dispatch` 会把包属性
    # dispatch 覆盖为同名函数, 故此处必须走 importlib 取子模块
    disp = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")

    async def _noop(**kwargs):
        return None

    async def _begin(**kwargs):
        return None

    monkeypatch.setattr(disp, "begin_dispatch", _begin)
    monkeypatch.setattr(disp, "record_dispatch", _noop)


def _capture_recording(monkeypatch, statuses: list):
    """静音统计落库,同时捕获 status 供断言"""
    import importlib

    disp = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")

    async def _begin(**kwargs):
        return 99

    async def _capture(**kwargs):
        statuses.append(kwargs.get("status"))
        return None

    monkeypatch.setattr(disp, "begin_dispatch", _begin)
    monkeypatch.setattr(disp, "record_dispatch", _capture)
