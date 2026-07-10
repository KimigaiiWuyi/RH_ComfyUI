"""SeedanceProviderChannel + 多通道组装 / 供应商固定 / 异常翻译 / Dry-Run 透传

验证 2026-07 的迁移:Seedance 多供应商由后端内部自建负载均衡改为
"每家供应商 = 一个 ProviderChannel",交给通用 LoadBalancer 调度。
"""

import asyncio
from typing import Any, Optional

import pytest

from RH_ComfyUI.core import SeedanceProviderChannel, channel_registry
from RH_ComfyUI.core.base.errors import ChannelError
from RH_ComfyUI.models.video.defs import (
    Seedance2Def,
    Seedance2MiniDef,
    Seedance15ProDef,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.utils.backends.seedance.channel import ProviderCredentials
from RH_ComfyUI.utils.backends.seedance.provider import (
    NormalizedTask,
    DryRunInterrupt,
    NormalizedStatus,
    SeedanceProvider,
    SeedanceProviderError,
    UnsupportedProviderShapeError,
)

# ── 测试用 fake provider ──


class _FakeProvider(SeedanceProvider):
    name = "fake"
    DEFAULT_BASE_URL = "http://fake.local"

    # 由测试通过类属性注入行为
    _raise: Optional[BaseException] = None

    async def render_create(self, spec, *, model):  # pragma: no cover - 不会被调用
        return "POST", self.base_url, {}, {}

    def parse_create(self, resp_json):  # pragma: no cover
        return "task-1"

    async def get(self, task_id):  # pragma: no cover
        return NormalizedTask(id=task_id, status=NormalizedStatus.SUCCEEDED)

    async def run(self, spec, *, model=None, on_progress=None):
        exc = type(self)._raise
        if exc is not None:
            raise exc
        return NormalizedTask(
            id="task-1",
            status=NormalizedStatus.SUCCEEDED,
            video_url="http://fake.local/out.mp4",
        )


def _creds(**kw: Any) -> Any:
    base: dict[str, Any] = {"enabled": True, "api_key": "k", "base_url": "http://fake.local"}
    base.update(kw)
    return lambda: ProviderCredentials(**base)


def _make_channel(name="fake", raise_exc=None, **cred_kw) -> SeedanceProviderChannel:
    cls = type(f"P_{name}", (_FakeProvider,), {"name": name, "_raise": raise_exc})
    return SeedanceProviderChannel(cls, weight=1, credentials_resolver=_creds(**cred_kw))


def _req() -> GenerationRequest:
    return GenerationRequest(task_type=TaskType.VIDEO, prompt="a cat")


# ── 多通道组装 ──


def test_builtin_bindings_ark_and_runninghub():
    channel_registry.clear()
    names = [b.channel.name for b in Seedance2Def().channel_bindings()]
    assert names == ["ark", "runninghub"]  # 二者都在 backend_models 中挂名

    # ark 携带 vendor model;runninghub 端点即模型 → vendor_model 为 None
    bindings = {b.channel.name: b for b in Seedance2Def().channel_bindings()}
    assert bindings["ark"].vendor_model == "doubao-seedance-2-0-260128"
    assert bindings["runninghub"].vendor_model is None


def test_mini_has_no_builtin_channels():
    channel_registry.clear()
    # backend_models={} → 内置供应商都不参与;未注册外部供应商 → 无通道
    assert Seedance2MiniDef().channel_bindings() == []


def test_external_channel_injected_and_participates():
    channel_registry.clear()
    gw = _make_channel(name="gateway")
    channel_registry.register_binding("seedance2", gw, vendor_model="doubao-seedance-2.0")
    channel_registry.register_binding("seedance2_mini", gw, vendor_model="doubao-seedance-2.0-mini")

    names = [b.channel.name for b in Seedance2Def().channel_bindings()]
    assert names == ["ark", "runninghub", "gateway"]

    mini = {b.channel.name: b for b in Seedance2MiniDef().channel_bindings()}
    assert list(mini) == ["gateway"]
    assert mini["gateway"].vendor_model == "doubao-seedance-2.0-mini"


def test_pinned_provider_selects_single_channel():
    channel_registry.clear()
    # 构造一个 provider 固定为 ark 的节点
    node = Seedance15ProDef.node_def()
    from dataclasses import replace

    from RH_ComfyUI.models.video.overrides import SeedanceVideoModel

    model = SeedanceVideoModel(replace(node, provider="ark"))
    bindings = model.channel_bindings()
    assert [b.channel.name for b in bindings] == ["ark"]
    assert bindings[0].vendor_model == "doubao-seedance-1-5-pro-251215"


# ── 异常翻译 ──


def _invoke(channel, model="m"):
    binding_vendor = model
    return asyncio.run(
        channel.invoke(request=_req(), node=None, on_progress=None, vendor_model=binding_vendor)
    )


def test_provider_error_retryable_propagates():
    # 供应商标 retryable=True(如任务在该家跑挂)→ 通道错误可切换
    err = SeedanceProviderError("boom", code="TASK_FAILED", retryable=True, user_message="供应商内部错误")
    ch = _make_channel(name="fake", raise_exc=err)
    with pytest.raises(ChannelError) as ei:
        _invoke(ch)
    assert ei.value.retryable is True
    assert ei.value.user_message == "供应商内部错误"
    assert ei.value.channel == "fake"


def test_provider_param_error_not_retryable():
    # 回归:供应商显式标 retryable=False(参数类 VID-* 错误)不得再盲目 failover
    err = SeedanceProviderError("参数无效", code="VID-INPUT_INVALID", retryable=False, user_message="参数无效")
    ch = _make_channel(name="fake", raise_exc=err)
    with pytest.raises(ChannelError) as ei:
        _invoke(ch)
    assert ei.value.retryable is False


def test_provider_429_maps_to_transient():
    # 429/503 → transient:run() 会先在原通道退避重试一次
    err = SeedanceProviderError("限流", code="HTTP_ERROR", retryable=True, http_status=429)
    ch = _make_channel(name="fake", raise_exc=err)
    with pytest.raises(ChannelError) as ei:
        _invoke(ch)
    assert ei.value.retryable is True and ei.value.transient is True


def test_http_status_retryable_policy():
    from RH_ComfyUI.utils.backends.seedance.provider import http_status_retryable

    assert http_status_retryable(500) and http_status_retryable(503)
    assert http_status_retryable(429) and http_status_retryable(401)
    assert not http_status_retryable(400) and not http_status_retryable(422)


def test_unsupported_shape_is_not_retryable():
    err = UnsupportedProviderShapeError("no such shape", code="UNSUPPORTED_SHAPE")
    ch = _make_channel(name="fake", raise_exc=err)
    with pytest.raises(ChannelError) as ei:
        _invoke(ch)
    assert ei.value.retryable is False


def test_dry_run_interrupt_propagates():
    ch = _make_channel(name="fake", raise_exc=DryRunInterrupt("dry"))
    with pytest.raises(DryRunInterrupt):
        _invoke(ch)


def test_missing_api_key_channel_unavailable():
    ch = _make_channel(name="fake", api_key="")
    assert asyncio.run(ch.check_available()) is False


# ── 通用扩展点:任意 bridge 模型都能接外部通道 ──


def test_bridge_model_accepts_external_channel():
    channel_registry.clear()
    from RH_ComfyUI.models.image.defs import GptImage2Def

    model = GptImage2Def()
    assert [b.channel.name for b in model.channel_bindings()] == ["gpt-image-2"]

    azure = _make_channel(name="azure")
    channel_registry.register_binding("gpt-image-2", azure, vendor_model="my-deploy")
    names = [b.channel.name for b in model.channel_bindings()]
    assert names == ["gpt-image-2", "azure"]


# ── 消费审计:记录供应商 key 前 6 位 ──


def test_audit_key_prefix():
    ch = _make_channel(name="fake", api_key="sk-abcdef123")
    assert ch.audit_key_prefix() == "sk-abc"
