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


def _mute_recording(monkeypatch):
    """统计落库依赖真实数据库,单测中静音"""
    import importlib

    # core/__init__ 中 `from .dispatch import dispatch` 会把包属性
    # dispatch 覆盖为同名函数, 故此处必须走 importlib 取子模块
    disp = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(disp, "record_dispatch", _noop)
