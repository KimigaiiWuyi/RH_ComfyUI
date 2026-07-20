"""通道能力预过滤 — `supports_request()` 钩子与 `AIGCGenerationBase.run()` 入口过滤

回归点:seedance2 等模型同时挂多个通道(ark / 网关 / aifoundation),
各通道对 resolution / ratio / duration 的支持不同。当用户传 1080P 而
aifoundation 仅支持 720P 时,`LoadBalancer` 此前可能把任务投到
aifoundation,触发 ``validate_spec`` 抛 ``UNSUPPORTED_RESOLUTION`` →
``ChannelError(retryable=False)`` → 整单失败。

修复后:`AIGCGenerationBase.run()` 在 `balancer.order_candidates()`
之前按 ``channel.supports_request(request)`` 预过滤,只把能力匹配的
通道交 LB 排序。
"""

import asyncio
from dataclasses import replace
from typing import Any, Optional

import pytest

from RH_ComfyUI.core.base.errors import ChannelError, ValidationError
from RH_ComfyUI.core.base.generation import AIGCGenerationBase
from RH_ComfyUI.core.channels.channel import ChannelBinding, ProviderChannel
from RH_ComfyUI.core.routing.balancer import BalancerConfig, LoadBalancer
from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.request import GenerationRequest, TaskType
from RH_ComfyUI.core.schema.types import NodeOutput, PortSpec, PortType
from RH_ComfyUI.utils.backends.seedance.channel import (
    ProviderCredentials,
    SeedanceProviderChannel,
)
from RH_ComfyUI.utils.backends.seedance.provider import (
    NormalizedStatus,
    NormalizedTask,
    SeedanceProvider,
    UnsupportedProviderShapeError,
)


# ── Fake Provider / Channel 工具 ──


class _FakeSeedanceProvider(SeedanceProvider):
    """最小可用的 SeedanceProvider 子类,供单元测试通过类属性注入能力矩阵"""

    name = "fake"
    DEFAULT_BASE_URL = "http://fake.local"
    # 各能力字段可由子类 type() 注入
    _supported_resolutions: set[str] = set()
    _supported_ratios: set[str] = set()
    _min_duration: int = 0
    _max_duration: int = 0
    _max_images: int = 9

    @property
    def supported_resolutions(self) -> set[str]:
        return type(self)._supported_resolutions

    @property
    def supported_ratios(self) -> set[str]:
        return type(self)._supported_ratios

    @property
    def min_duration(self) -> int:
        return type(self)._min_duration

    @property
    def max_duration(self) -> int:
        return type(self)._max_duration

    @property
    def max_images(self) -> int:
        return type(self)._max_images

    async def render_create(self, spec, *, model):  # pragma: no cover - 不会被调用
        return "POST", self.base_url, {}, {}

    def parse_create(self, resp_json):  # pragma: no cover
        return "task-1"

    async def get(self, task_id):  # pragma: no cover
        return NormalizedTask(id=task_id, status=NormalizedStatus.SUCCEEDED)

    async def run(self, spec, *, model=None, on_progress=None):
        return NormalizedTask(
            id="task-1",
            status=NormalizedStatus.SUCCEEDED,
            video_url="http://fake.local/out.mp4",
        )


def _creds(**kw: Any) -> Any:
    base: dict[str, Any] = {"enabled": True, "api_key": "k", "base_url": "http://fake.local"}
    base.update(kw)
    return lambda: ProviderCredentials(**base)


def _make_seedance_channel(
    name: str,
    *,
    resolutions: set[str] = frozenset(),
    ratios: set[str] = frozenset(),
    min_dur: int = 0,
    max_dur: int = 0,
    max_imgs: int = 9,
) -> SeedanceProviderChannel:
    """构造一个 SeedanceProviderChannel,能力矩阵通过类属性注入。

    用 type() 派生新类(避免实例属性覆盖类属性的隐式陷阱)。
    """
    cls = type(
        f"_FakeProvider_{name}",
        (_FakeSeedanceProvider,),
        {
            "name": name,
            "_supported_resolutions": set(resolutions),
            "_supported_ratios": set(ratios),
            "_min_duration": min_dur,
            "_max_duration": max_dur,
            "_max_images": max_imgs,
        },
    )
    return SeedanceProviderChannel(cls, weight=1, credentials_resolver=_creds())


# ── 1. ProviderChannel 基类默认 supports_request ──


class _BareChannel(ProviderChannel):
    """不 override supports_request 的 ProviderChannel"""

    name = "bare"

    async def check_available(self) -> bool:
        return True

    async def invoke(self, **kwargs):
        return NodeOutput(output_type="image", data=b"ok")


def test_provider_channel_default_supports_request_returns_true():
    """基类默认 supports_request() 返回 True(向后兼容)"""
    ch = _BareChannel()
    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")
    assert ch.supports_request(req) is True


# ── 2. SeedanceProvider.can_handle_spec 维度 ──


def _video_spec(resolution: Optional[str] = None, ratio: Optional[str] = None, duration: int = 5):
    """构造一个 VideoGenSpec,避开 classify 完整路径,直接构造 dataclass"""
    from RH_ComfyUI.utils.backends.seedance.spec import VideoGenSpec, VideoTaskShape

    return VideoGenSpec(
        shape=VideoTaskShape.TEXT2VIDEO,
        prompt="a cat",
        media=[],
        resolution=resolution,
        ratio=ratio,
        duration=duration,
    )


def test_can_handle_spec_resolution_in_set():
    """resolution 在 supported_resolutions 内 → True"""
    provider = type(
        "_P", (_FakeSeedanceProvider,), {"name": "p720", "_supported_resolutions": {"480p", "720p"}}
    )()
    assert provider.can_handle_spec(_video_spec(resolution="720p")) is True
    assert provider.can_handle_spec(_video_spec(resolution="480p")) is True


def test_can_handle_spec_resolution_not_in_set_returns_false():
    """resolution 不在 supported_resolutions 内 → False"""
    provider = type(
        "_P", (_FakeSeedanceProvider,), {"name": "p720", "_supported_resolutions": {"480p", "720p"}}
    )()
    assert provider.can_handle_spec(_video_spec(resolution="1080p")) is False


def test_can_handle_spec_resolution_undeclared_allows_all():
    """supported_resolutions 为空(=未声明)时所有 resolution 都通过"""
    provider = type("_P", (_FakeSeedanceProvider,), {"name": "pall"})()
    for res in ("480p", "720p", "1080p", "4k"):
        assert provider.can_handle_spec(_video_spec(resolution=res)) is True


def test_can_handle_spec_skips_media_count_check():
    """媒体数超出 max_images 仍返回 True(用户要求:不按媒体数过滤)"""
    from RH_ComfyUI.utils.backends.seedance.spec import (
        MediaRef,
        MediaRole,
        SpecMedia,
        VideoGenSpec,
        VideoTaskShape,
    )
    from RH_ComfyUI.utils.core.types import MediaKind

    three_images = [
        SpecMedia(kind=MediaKind.IMAGE, role=MediaRole.REFERENCE, ref=MediaRef(kind=MediaKind.IMAGE, data=b"x"), index=0),
        SpecMedia(kind=MediaKind.IMAGE, role=MediaRole.REFERENCE, ref=MediaRef(kind=MediaKind.IMAGE, data=b"y"), index=1),
        SpecMedia(kind=MediaKind.IMAGE, role=MediaRole.REFERENCE, ref=MediaRef(kind=MediaKind.IMAGE, data=b"z"), index=2),
    ]
    spec = VideoGenSpec(
        shape=VideoTaskShape.IMAGE2VIDEO,
        prompt="a cat",
        media=three_images,  # 3 张图
    )
    provider = type(
        "_P",
        (_FakeSeedanceProvider,),
        {"name": "plimit", "max_images": 2},  # 受限:只许 2 张
    )()
    assert provider.can_handle_spec(spec) is True  # can_handle_spec 故意不检媒体数


def test_can_handle_spec_ratio_when_declared():
    """声明 supported_ratios 后正确判定"""
    provider = type(
        "_P", (_FakeSeedanceProvider,), {"name": "p9x16", "_supported_ratios": {"9:16", "1:1"}}
    )()
    assert provider.can_handle_spec(_video_spec(ratio="9:16")) is True
    assert provider.can_handle_spec(_video_spec(ratio="21:9")) is False


def test_can_handle_spec_ratio_undeclared_allows_all():
    """supported_ratios 未声明时所有 ratio 都通过"""
    provider = type("_P", (_FakeSeedanceProvider,), {"name": "pall"})()
    for ratio in ("16:9", "9:16", "1:1", "21:9"):
        assert provider.can_handle_spec(_video_spec(ratio=ratio)) is True


def test_can_handle_spec_duration_min_max():
    """声明 min/max_duration 后正确判定"""
    provider = type(
        "_P",
        (_FakeSeedanceProvider,),
        {"name": "pdur", "_min_duration": 4, "_max_duration": 15},
    )()
    assert provider.can_handle_spec(_video_spec(duration=10)) is True
    assert provider.can_handle_spec(_video_spec(duration=3)) is False  # 低于下限
    assert provider.can_handle_spec(_video_spec(duration=16)) is False  # 高于上限


def test_can_handle_spec_duration_undeclared_allows_all():
    """min/max_duration 都为 0 时所有 duration 都通过"""
    provider = type("_P", (_FakeSeedanceProvider,), {"name": "pall"})()
    for dur in (1, 5, 15, 30, 60):
        assert provider.can_handle_spec(_video_spec(duration=dur)) is True


# ── 3. validate_spec 新增 code ──


def test_validate_spec_unsupported_ratio_raises():
    """UNSUPPORTED_RATIO 抛出与 code 字段"""
    provider = type(
        "_P", (_FakeSeedanceProvider,), {"name": "pratio", "_supported_ratios": {"9:16"}}
    )()
    with pytest.raises(UnsupportedProviderShapeError) as ei:
        provider.validate_spec(_video_spec(ratio="21:9"))
    assert ei.value.code == "UNSUPPORTED_RATIO"


def test_validate_spec_unsupported_duration_raises():
    """UNSUPPORTED_DURATION 抛出与 code 字段"""
    provider = type(
        "_P",
        (_FakeSeedanceProvider,),
        {"name": "pdur", "_min_duration": 4, "_max_duration": 15},
    )()
    with pytest.raises(UnsupportedProviderShapeError) as ei:
        provider.validate_spec(_video_spec(duration=30))
    assert ei.value.code == "UNSUPPORTED_DURATION"


# ── 4. SeedanceProviderChannel.supports_request ──


def test_seedance_channel_supports_request_filters_by_resolution():
    """SeedanceProviderChannel.supports_request() 复用 provider.can_handle_spec"""
    ch_720 = _make_seedance_channel("ch_720", resolutions={"480p", "720p"})
    ch_full = _make_seedance_channel("ch_full", resolutions={"480p", "720p", "1080p"})

    req_720 = GenerationRequest(task_type=TaskType.VIDEO, prompt="x", resolution="720p")
    req_1080 = GenerationRequest(task_type=TaskType.VIDEO, prompt="x", resolution="1080p")

    # 720p 请求:两个通道都能接
    assert ch_720.supports_request(req_720) is True
    assert ch_full.supports_request(req_720) is True

    # 1080p 请求:仅 ch_full 能接
    assert ch_720.supports_request(req_1080) is False
    assert ch_full.supports_request(req_1080) is True


def test_seedance_channel_supports_request_swallows_exceptions():
    """任何异常保守返回 True(凭证切换中短暂 None 等边角情况)"""
    ch = _make_seedance_channel("ch_x", resolutions={"720p"})
    # 故意构造一个会让 classify 异常的 request(此处无法构造,改测 None provider)
    # 直接验证 _get_provider() 异常时不抛
    from unittest.mock import patch

    with patch.object(ch, "_get_provider", return_value=None):
        req = GenerationRequest(task_type=TaskType.VIDEO, prompt="x", resolution="1080p")
        # None provider → 返回 True(放行),由 check_available + invoke 兜底
        assert ch.supports_request(req) is True


# ── 5. AIGCGenerationBase.run() 入口预过滤 ──


class _SupportsRequestChannel(ProviderChannel):
    """测试用 Channel:supports_request 由构造参数控制"""

    def __init__(self, name: str, *, can_handle: bool, invoked: list[str]):
        self.name = name
        self._can_handle = can_handle
        self._invoked = invoked

    async def check_available(self) -> bool:
        return True

    def supports_request(self, request):
        return self._can_handle

    async def invoke(self, **kwargs):
        self._invoked.append(self.name)
        return NodeOutput(output_type="image", data=f"from-{self.name}".encode())


class _TwoChannelCapabilityModel(AIGCGenerationBase):
    """测试用模型:两个通道,supports_request 各异"""

    def __init__(self, ch_a: ProviderChannel, ch_b: ProviderChannel) -> None:
        self.name = "capability_model"
        self.display_name = "capability_model"
        self._ch_a = ch_a
        self._ch_b = ch_b

    def input_schema(self) -> dict:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(self._ch_a), ChannelBinding(self._ch_b)]

    async def execute_on_channel(self, request, binding, *, on_progress=None):
        return await binding.channel.invoke(request=request)

    def balancer(self):
        return LoadBalancer(BalancerConfig(mode="least_failures"))


def test_model_run_skips_incapable_channel():
    """supports_request=False 的通道不会被 invoke()"""
    invoked: list[str] = []
    ch_720 = _SupportsRequestChannel("ch_720", can_handle=False, invoked=invoked)
    ch_full = _SupportsRequestChannel("ch_full", can_handle=True, invoked=invoked)
    model = _TwoChannelCapabilityModel(ch_720, ch_full)

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")
    out = asyncio.run(model.run(req))
    assert out.data == b"from-ch_full"
    assert invoked == ["ch_full"]  # ch_720 被预过滤掉


def test_model_run_raises_validation_error_when_all_channels_filtered():
    """全部通道因能力不兼容被排除 → 抛 ValidationError"""
    invoked: list[str] = []
    ch_a = _SupportsRequestChannel("ch_a", can_handle=False, invoked=invoked)
    ch_b = _SupportsRequestChannel("ch_b", can_handle=False, invoked=invoked)
    model = _TwoChannelCapabilityModel(ch_a, ch_b)

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")
    with pytest.raises(ValidationError) as ei:
        asyncio.run(model.run(req))
    assert "所有通道均无法处理" in str(ei.value)
    assert "ch_a" in str(ei.value) and "ch_b" in str(ei.value)
    assert invoked == []  # 没有任何通道被 invoke


def test_model_run_no_filter_when_all_channels_capable():
    """所有通道都能处理时,LoadBalancer 正常调度"""
    invoked: list[str] = []
    ch_a = _SupportsRequestChannel("ch_a", can_handle=True, invoked=invoked)
    ch_b = _SupportsRequestChannel("ch_b", can_handle=True, invoked=invoked)
    model = _TwoChannelCapabilityModel(ch_a, ch_b)

    req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi")
    out = asyncio.run(model.run(req))
    assert out.data is not None
    # least_failures 模式下两个都 0 失败,排序按列表顺序(priority 相同)
    assert len(invoked) == 1
    assert invoked[0] in ("ch_a", "ch_b")


# ── 6. 端到端:Seedance 多通道 + 1080p 场景 ──


class _EndToEndModel(AIGCGenerationBase):
    """真实场景模拟:模型挂 ark(支持 1080p) + aifoundation(仅 720p) 两个通道"""

    def __init__(self, ch_ark: ProviderChannel, ch_aif: ProviderChannel) -> None:
        self.name = "seedance2"
        self.display_name = "Seedance 2.0"
        self._ch_ark = ch_ark
        self._ch_aif = ch_aif

    def input_schema(self) -> dict:
        # 模拟 defs.py:Seedance2Def.input_schema() 声明 [480p, 720p, 1080p]
        return {
            "prompt": PortSpec(type=PortType.TEXT, required=True),
            "resolution": PortSpec(
                type=PortType.ENUM,
                values=["480p", "720p", "1080p"],
            ),
        }

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(self._ch_ark), ChannelBinding(self._ch_aif)]

    async def execute_on_channel(self, request, binding, *, on_progress=None):
        return await binding.channel.invoke(request=request)

    def balancer(self):
        return LoadBalancer(BalancerConfig(mode="least_failures"))


class _CapabilityStubChannel(ProviderChannel):
    """轻量 ProviderChannel:supports_request 由构造参数决定,invoke 仅记录调用名。

    避免使用 SeedanceProviderChannel 触发真实的 _download() 网络请求。
    """

    def __init__(self, name: str, *, supports: bool, recorder: list[str]) -> None:
        self.name = name
        self._supports = supports
        self._recorder = recorder

    async def check_available(self) -> bool:
        return True

    def supports_request(self, request):
        return self._supports

    async def invoke(self, **kwargs):
        self._recorder.append(self.name)
        return NodeOutput(output_type="video", data=f"video-from-{self.name}".encode())


def test_end_to_end_1080p_routes_to_capable_channel():
    """用户传 1080p:仅 ark 通道能承接,aifoundation 被预过滤"""
    invoked: list[str] = []
    # 模拟 ark 支持 1080p,aifoundation 仅 720p
    ch_ark = _CapabilityStubChannel("ark", supports=True, recorder=invoked)
    ch_aif = _CapabilityStubChannel("aifoundation", supports=False, recorder=invoked)
    model = _EndToEndModel(ch_ark, ch_aif)

    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="a cat", resolution="1080p")
    out = asyncio.run(model.run(req))
    assert invoked == ["ark"], f"期望只命中 ark,实际 {invoked}"
    assert out.data == b"video-from-ark"


def test_end_to_end_720p_can_route_to_either():
    """用户传 720p:两个通道都能承接,LB 调度命中其一"""
    invoked: list[str] = []
    ch_ark = _CapabilityStubChannel("ark", supports=True, recorder=invoked)
    ch_aif = _CapabilityStubChannel("aifoundation", supports=True, recorder=invoked)
    model = _EndToEndModel(ch_ark, ch_aif)

    req = GenerationRequest(task_type=TaskType.VIDEO, prompt="a cat", resolution="720p")
    asyncio.run(model.run(req))
    # 720p 两个通道都通过预过滤,LB 命中其一
    assert len(invoked) == 1
    assert invoked[0] in ("ark", "aifoundation")