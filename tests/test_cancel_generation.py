"""取消生成:活跃任务注册表 + Ark delete + seedance2_mini 补全"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from RH_ComfyUI.core.dispatch.active_tasks import (
    ActiveTaskRegistry,
    cancel_generation,
    get_active_task_registry,
)


def test_cancel_by_trace_id_cancels_local_task():
    async def _run() -> None:
        reg = ActiveTaskRegistry()

        async def _worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.create_task(_worker())
        await reg.register(
            model_name="seedance2",
            trace_id="trace-cancel-1",
            record_id=42,
            task=task,
        )

        remote = AsyncMock()
        await reg.bind_vendor_cancel(
            vendor_task_id="cgt-123",
            cancel_remote=remote,
            channel_name="ark",
            ag=reg.get_by_trace("trace-cancel-1"),
        )

        result = await reg.cancel(trace_id="trace-cancel-1", reason="test")
        assert result["found"] is True
        assert result["cancelled_local"] is True
        assert result["cancelled_remote"] is True
        assert result["vendor_task_id"] == "cgt-123"
        remote.assert_awaited_once()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled() or task.done()

    asyncio.run(_run())


def test_cancel_missing_task():
    result = asyncio.run(cancel_generation(trace_id="does-not-exist"))
    assert result["found"] is False
    assert result["ok"] is False


def test_cancel_already_done_task_not_ok():
    """任务已结束后 cancel 不得 ok=true,也不得再 remote DELETE。"""

    async def _run() -> None:
        reg = ActiveTaskRegistry()

        async def _worker() -> None:
            return None

        task = asyncio.create_task(_worker())
        await task  # 先完成
        await reg.register(
            model_name="seedance2",
            trace_id="trace-done-1",
            record_id=99,
            task=task,
        )
        remote = AsyncMock()
        await reg.bind_vendor_cancel(
            vendor_task_id="cgt-done",
            cancel_remote=remote,
            channel_name="ark",
            ag=reg.get_by_trace("trace-done-1"),
        )
        result = await reg.cancel(trace_id="trace-done-1")
        assert result["found"] is True
        assert result["ok"] is False
        assert result["cancelled_local"] is False
        assert result["cancelled_remote"] is False
        remote.assert_not_awaited()

    asyncio.run(_run())


def test_channel_remote_cancel_distinguishes_rh_app_and_comfyui():
    """remote cancel 是供应商/通道级;未知通道 fail-closed;不读模型 ClassVar。"""
    from RH_ComfyUI.models.bridge import AdapterChannel
    from RH_ComfyUI.rh_models.api import _channel_supports_remote_cancel
    from RH_ComfyUI.utils.backends.seedance.channel import SeedanceProviderChannel
    from RH_ComfyUI.utils.backends.seedance.providers.ark import ArkSeedanceProvider
    from RH_ComfyUI.utils.backends.seedance.providers.runninghub import (
        RunningHubSeedanceProvider,
    )

    class _ComfyModel:
        supports_remote_cancel = False  # 故意 False:remote 不读模型 ClassVar
        node = type("N", (), {"backend": "comfyui"})()

    class _RhAppModel:
        supports_remote_cancel = True  # 故意 True:rh_app 仍强制 False
        node = type("N", (), {"backend": "rh_app"})()

    class _SeedanceModel:
        supports_remote_cancel = False
        node = type("N", (), {"backend": "seedance"})()

    assert _channel_supports_remote_cancel(_ComfyModel(), "comfyui") is True
    assert _channel_supports_remote_cancel(_RhAppModel(), "rh_app") is False
    assert _channel_supports_remote_cancel(_SeedanceModel(), "ark") is True
    # Seedance RH 视频端点无 cancel(与 Comfy 工作流 runninghub 同名不同 API)
    assert _channel_supports_remote_cancel(_SeedanceModel(), "runninghub") is False
    assert _channel_supports_remote_cancel(_ComfyModel(), "runninghub") is True
    # 未知通道/空名 → False
    assert _channel_supports_remote_cancel(_SeedanceModel(), "unknown_vendor_xyz") is False
    assert _channel_supports_remote_cancel(_SeedanceModel(), "") is False

    # 通道实例:provider 级 override 优先于名字矩阵
    ark_ch = SeedanceProviderChannel(ArkSeedanceProvider)
    rh_ch = SeedanceProviderChannel(RunningHubSeedanceProvider)
    assert ark_ch.supports_remote_cancel() is True
    assert rh_ch.supports_remote_cancel() is False
    assert (
        _channel_supports_remote_cancel(_SeedanceModel(), "ark", channel=ark_ch) is True
    )
    assert (
        _channel_supports_remote_cancel(_SeedanceModel(), "runninghub", channel=rh_ch)
        is False
    )
    assert AdapterChannel("comfyui").supports_remote_cancel() is True
    assert AdapterChannel("rh_app").supports_remote_cancel() is False
    # 网关异步视频/图:名字兜底
    assert _channel_supports_remote_cancel(_SeedanceModel(), "gateway_slot1_seedance") is True
    assert (
        _channel_supports_remote_cancel(_ComfyModel(), "gateway_slot1_gpt_image_2") is True
    )
    assert (
        _channel_supports_remote_cancel(_ComfyModel(), "gateway_slot1_seedream5") is False
    )


def test_can_resume_requires_vendor_task_id():
    from RH_ComfyUI.core.dispatch.resume import can_resume

    assert can_resume(backend="seedance", vendor_task_id="tid-1") is True
    assert can_resume(backend="rh_app", vendor_task_id="tid-1") is True
    assert can_resume(backend="comfyui", vendor_task_id="pid") is True
    assert can_resume(backend="minimax-h3", vendor_task_id="tid-1") is True
    assert can_resume(backend="seedance", vendor_task_id="") is False


def test_all_channels_support_cancel_helper():
    from RH_ComfyUI.rh_models.api import _all_channels_support_cancel

    assert _all_channels_support_cancel([]) is False
    assert (
        _all_channels_support_cancel(
            [
                {"name": "ark", "supports_cancel": True},
                {"name": "runninghub", "supports_cancel": True},
            ]
        )
        is True
    )
    assert (
        _all_channels_support_cancel(
            [
                {"name": "ark", "supports_cancel": True},
                {"name": "rh_app", "supports_cancel": False},
            ]
        )
        is False
    )
    assert _all_channels_support_cancel([{"name": "a"}]) is False


def test_prompt_in_comfy_queue_parses_shapes():
    from RH_ComfyUI.utils.backends.comfyui.api import ComfyUIAPI

    q = [[1, "pid-a", {}], [2, "pid-b", {}]]
    assert ComfyUIAPI._prompt_in_comfy_queue(q, "pid-a") is True
    assert ComfyUIAPI._prompt_in_comfy_queue(q, "pid-z") is False
    assert ComfyUIAPI._prompt_in_comfy_queue([{"prompt_id": "pid-c"}], "pid-c") is True
    assert ComfyUIAPI._prompt_in_comfy_queue(None, "x") is False


def test_resume_client_from_channel_prefers_public_protocol():
    """get_resume_client 优先于私有 _client。"""
    from RH_ComfyUI.core.dispatch.resume import (
        ResumeNotSupportedError,
        _resume_client_from_channel,
    )

    class _Public:
        def get_resume_client(self) -> object:
            return {"kind": "public"}

        _client = {"kind": "private"}

    class _LegacyOnly:
        _client = {"kind": "legacy"}

    class _Empty:
        pass

    assert _resume_client_from_channel(_Public(), "ch") == {"kind": "public"}
    assert _resume_client_from_channel(_LegacyOnly(), "ch") == {"kind": "legacy"}
    try:
        _resume_client_from_channel(_Empty(), "ch")
        raise AssertionError("expected ResumeNotSupportedError")
    except ResumeNotSupportedError as exc:
        assert "ch" in str(exc)


def test_infer_backend_channel_over_node_backend():
    """gateway 通道优先于 node.backend;gpt-image-2 不伪装成 gemini。"""
    from RH_ComfyUI.core.dispatch.resume import can_resume, _infer_backend

    assert (
        _infer_backend(
            backend="gemini-image",
            model="banana2",
            channel="gateway_slot1_gemini_2_5_flash_image",
        )
        == "gateway-image"
    )
    assert (
        _infer_backend(
            backend="gpt-image-2",
            model="gpt-image-2",
            channel="gateway_slot1_gpt_image_2",
        )
        == "gateway-image"
    )
    assert _infer_backend(backend="gpt-image-2", model="gpt-image-2", channel="") == "gpt-image-2"
    assert can_resume(backend="gpt-image-2", vendor_task_id="x") is False
    assert can_resume(channel="gateway_slot1_gpt_image_2", vendor_task_id="x") is True
    assert (
        _infer_backend(backend="", model="seedance2", channel="gateway_slot1_seedance")
        == "seedance"
    )
    assert _infer_backend(backend="", model="minimax_h3", channel="") == "minimax-h3"
    assert (
        _infer_backend(backend="", model="minimax_h3", channel="minimax-h3") == "minimax-h3"
    )


def test_resolve_seedance_channel_hard_fail_on_missing():
    from RH_ComfyUI.core.dispatch.resume import (
        ResumeNotSupportedError,
        _resolve_seedance_channel,
    )

    try:
        _resolve_seedance_channel("gateway_slot99_nope", "seedance2")
        raise AssertionError("expected ResumeNotSupportedError")
    except ResumeNotSupportedError as exc:
        assert "gateway_slot99_nope" in str(exc)


def test_cancel_without_remote_reports_skip():
    """create 前取消:仅本地 cancel,message 标明无上游。"""

    async def _run() -> None:
        reg = ActiveTaskRegistry()

        async def _worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.create_task(_worker())
        await reg.register(
            model_name="seedance2",
            trace_id="trace-no-remote",
            record_id=7,
            task=task,
        )
        result = await reg.cancel(trace_id="trace-no-remote", reason="test")
        assert result["found"] is True
        assert result["cancelled_local"] is True
        assert result["cancelled_remote"] is False
        assert result.get("remote_skip")
        assert "尚未 bind" in str(result.get("remote_skip") or result.get("message"))
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


def test_ark_provider_overrides_delete():
    from RH_ComfyUI.utils.backends.seedance.provider import SeedanceProvider
    from RH_ComfyUI.utils.backends.seedance.providers.ark import ArkSeedanceProvider

    assert ArkSeedanceProvider.delete is not SeedanceProvider.delete
    p = ArkSeedanceProvider(api_key="k", base_url=ArkSeedanceProvider.DEFAULT_BASE_URL)
    assert p.supports_remote_cancel() is True


def test_seedance2_mini_aligned_with_ark():
    from RH_ComfyUI.models.video.defs import Seedance2FastDef, Seedance2MiniDef

    mini = Seedance2MiniDef()
    fast = Seedance2FastDef()
    assert mini.name == "seedance2_mini"
    assert mini.node.backend_model == "doubao-seedance-2-0-mini-260615"
    assert mini.node.backend_models.get("ark") == "doubao-seedance-2-0-mini-260615"
    assert "ark" in (mini.node.backend_models or {})
    # 分辨率面与 Fast 一致(无 1080p)
    res_values = list(mini.node.inputs["resolution"].values or [])
    assert "1080p" not in res_values
    assert set(res_values) == set(fast.node.inputs["resolution"].values or [])
    assert mini.supports_remote_cancel is True
    assert mini.execution_mode == "async_poll"


def test_seedance_models_declare_remote_cancel():
    from RH_ComfyUI.models.video.defs import (
        Seedance2Def,
        Seedance25Def,
        Seedance2FastDef,
        Seedance2MiniDef,
        Seedance15ProDef,
    )

    for cls in (Seedance2Def, Seedance25Def, Seedance2FastDef, Seedance15ProDef, Seedance2MiniDef):
        m = cls()
        assert m.supports_cancel is True
        assert m.supports_remote_cancel is True


def test_comfyui_and_gemini_models_declare_remote_cancel():
    """comfyui 声明 remote cancel;gemini 生图改为 generate_content 后无远程 cancel;rh_app 也不声明。"""
    from RH_ComfyUI.models.image.defs import (
        AnimaDef,
        Banana2Def,
        Qwen2512Def,
        CameraAngleDef,
    )
    from RH_ComfyUI.models.music.defs import AceStep15Def
    from RH_ComfyUI.models.video.defs import Wan22VideogenDef
    from RH_ComfyUI.models.speech.defs import IndexTTS2Def, IndexTTS25Def

    for cls in (Qwen2512Def, Wan22VideogenDef, IndexTTS2Def, AceStep15Def):
        m = cls()
        assert m.supports_cancel is True, m.name
        assert m.supports_remote_cancel is True, f"{m.name} backend={m.node.backend}"

    banana2 = Banana2Def()
    assert banana2.supports_cancel is True
    assert banana2.supports_remote_cancel is False

    # rh_app:禁止取消(本地/远程),只能 resume 继续轮询
    for cls in (AnimaDef, CameraAngleDef, IndexTTS25Def):
        m = cls()
        assert m.supports_cancel is False, m.name
        assert m.supports_remote_cancel is False, m.name


def test_comfyui_api_has_cancel_paths():
    from RH_ComfyUI.utils.backends.comfyui.api import ComfyUIAPI

    assert callable(ComfyUIAPI.cancel_task)
    assert callable(ComfyUIAPI._cancel_local)
    assert callable(ComfyUIAPI._cancel_runninghub)
    # /task/openapi/cancel 仅 ComfyUI 工作流,不在 rh_app AI 应用客户端
    from RH_ComfyUI.utils.backends.rh_app import api as rh_api

    assert not hasattr(rh_api.RHAppAPI, "cancel_task")


def test_gemini_generate_content_uses_canonical_model(monkeypatch):
    """生图走 generate_content,并把误带的 -agent 后缀剥掉。"""
    import RH_ComfyUI.utils.backends.gemini_image.api as gapi

    seen: dict[str, object] = {}

    class _Part:
        def __init__(self) -> None:
            self.inline_data = type("D", (), {"data": b"\x89PNG", "uri": None})()
            self.file_data = None
            self.text = None

    class _AioModels:
        async def generate_content(self, **kwargs):
            seen.update(kwargs)
            return type(
                "R",
                (),
                {"candidates": [type("C", (), {"content": type("K", (), {"parts": [_Part()]})()})()]},
            )()

    class _Aio:
        models = _AioModels()

    class _Client:
        aio = _Aio()

    monkeypatch.setattr(gapi.GeminiImageAPI, "_build_client", lambda self: _Client())
    api = gapi.GeminiImageAPI()
    data = asyncio.run(api.generate(model="gemini-3.1-flash-image-preview-agent", prompt="cat"))
    assert data == b"\x89PNG"
    assert seen["model"] == "gemini-3.1-flash-image-preview"


def test_happyhorse_cancel_uses_dashscope_post_cancel():
    """DashScope 官方: POST /api/v1/tasks/{id}/cancel(仅 PENDING 可取消)。"""
    from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProvider

    p = HappyHorseProvider(api_key="sk-test", base_url=HappyHorseProvider.DEFAULT_BASE_URL)
    seen: dict = {}

    async def _fake_request(method, url, headers=None, json=None):
        seen["method"] = method
        seen["url"] = url
        seen["headers"] = headers
        return {"request_id": "req-1"}

    p._request = _fake_request  # type: ignore[method-assign]
    asyncio.run(p.delete("73205176-xxxx-xxxx-xxxx-16bd5d902219"))
    assert seen["method"] == "POST"
    assert seen["url"] == ("https://dashscope.aliyuncs.com/api/v1/tasks/73205176-xxxx-xxxx-xxxx-16bd5d902219/cancel")
    assert (seen["headers"] or {}).get("Authorization", "").startswith("Bearer ")


def test_dispatch_registers_and_cancel(monkeypatch):
    """dispatch 登记 active task;cancel 会触发 CancelledError 路径。"""
    import importlib

    # core.dispatch 包属性 `dispatch` 是函数,遮蔽了子模块;用 importlib 拿真正的 dispatcher 模块
    dispatcher_mod = importlib.import_module("RH_ComfyUI.core.dispatch.dispatcher")
    recorder_mod = importlib.import_module("RH_ComfyUI.core.telemetry.recorder")

    from RH_ComfyUI.core.schema.card import ModelCard
    from RH_ComfyUI.core.schema.types import PortSpec, PortType, NodeOutput
    from RH_ComfyUI.core.billing.policy import BillingPolicy, BillingContext, BillingReservation
    from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
    from RH_ComfyUI.core.base.generation import AIGCGenerationBase
    from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding
    from RH_ComfyUI.core.dispatch.context import DispatchContext
    from RH_ComfyUI.core.routing.registry import model_registry

    dispatch = dispatcher_mod.dispatch

    class _SlowModel(AIGCGenerationBase):
        name = "slow_cancel_model"
        display_name = "Slow"
        modality = TaskType.IMAGE
        card = ModelCard(description="test")
        point_cost = 1
        supports_cancel = True

        def input_schema(self) -> dict[str, PortSpec]:
            return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

        def channel_bindings(self) -> list[ChannelBinding]:
            return [ChannelBinding(LocalChannel("local"))]

        async def execute_on_channel(
            self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
        ) -> NodeOutput:
            await asyncio.sleep(30)
            return NodeOutput(status="ok", output_type="image", data=b"x", mime_type="image/png")

    class _Policy(BillingPolicy):
        async def reserve(self, ctx: BillingContext, cost: int) -> BillingReservation:
            return BillingReservation(cost=cost, context=ctx)

        async def refund(self, reservation: BillingReservation) -> None:
            reservation.refunded = True

    model = _SlowModel()
    model_registry.register(model)
    try:
        monkeypatch.setattr(recorder_mod, "begin_dispatch", AsyncMock(return_value=99))
        monkeypatch.setattr(recorder_mod, "record_dispatch", AsyncMock())
        monkeypatch.setattr(dispatcher_mod, "_resolve_timeout", lambda: 0.0)

        req = GenerationRequest(task_type=TaskType.IMAGE, prompt="hi", model="slow_cancel_model")
        req.trace_id = "dispatch-cancel-trace"
        ctx = DispatchContext(
            billing=BillingContext(user_id="u1", bot_id="b", entry_point="http"),
            policy=_Policy(),
            trace_id="dispatch-cancel-trace",
        )

        async def _run_and_cancel() -> dict:
            task = asyncio.create_task(dispatch(req, ctx))
            for _ in range(50):
                if get_active_task_registry().get_by_trace("dispatch-cancel-trace"):
                    break
                await asyncio.sleep(0.02)
            result = await cancel_generation(trace_id="dispatch-cancel-trace")
            assert result["found"] is True
            with pytest.raises(asyncio.CancelledError):
                await task
            return result

        out = asyncio.run(_run_and_cancel())
        assert out["cancelled_local"] is True
    finally:
        model_registry.unregister(model.name)


def test_seedance_bind_only_remote_when_delete_overridden():
    """no-op delete 的 provider 只落库 id,不挂 cancel_remote。"""
    from RH_ComfyUI.utils.backends.seedance.provider import SeedanceProvider

    class _Noop(SeedanceProvider):
        name = "noop-seedance"

        async def render_create(self, spec, *, model=None):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def parse_create(self, resp):  # type: ignore[no-untyped-def]
            return ""

        async def get(self, task_id: str):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    async def _run() -> None:
        reg = ActiveTaskRegistry()
        ag = await reg.register(model_name="seedance2", trace_id="t-noop", record_id=1)
        p = _Noop(api_key="k", base_url="https://example.com")
        from unittest.mock import patch

        with patch(
            "RH_ComfyUI.core.dispatch.active_tasks.get_active_task_registry",
            lambda: reg,
        ):
            await p._bind_active_cancel("tid-noop")
        assert ag.vendor_task_id == "tid-noop"
        assert ag.cancel_remote is None
        assert p.supports_remote_cancel() is False

    asyncio.run(_run())


def _make_finalize_session_factory(
    *,
    row: object,
    update_rowcount: int = 1,
    updates: list | None = None,
):
    """构造 async_maker 替身:select 返回 row,Update 记 values 并返回 rowcount。

    按 stmt 类型区分(两阶段 finalize 会开第二个 session 只跑 UPDATE)。
    """

    class _SelectResult:
        def scalar_one_or_none(self):
            return row

    class _UpdateResult:
        def __init__(self, n: int):
            self.rowcount = n

    def _is_update(stmt: object) -> bool:
        try:
            from sqlalchemy.sql.dml import Update as SAUpdate

            if isinstance(stmt, SAUpdate):
                return True
        except Exception:  # noqa: BLE001
            pass
        return type(stmt).__name__ == "Update"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, stmt):
            if _is_update(stmt):
                if updates is not None:
                    try:
                        updates.append(dict(stmt.compile().params))
                    except Exception:  # noqa: BLE001
                        updates.append({"raw": str(stmt)})
                return _UpdateResult(update_rowcount)
            return _SelectResult()

        async def commit(self):
            return None

    class _Maker:
        def __call__(self):
            return _Session()

    return _Maker()


def test_resume_finalize_refunds_points(monkeypatch):
    """resume 失败:running + 非 http → 先 CAS 终态,钱包成功后再 mark refunded。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    refund_calls: list[dict] = []
    updates: list = []

    class _Row:
        status = "running"
        refunded = False
        point_cost = 12
        user_id = "u1"
        bot_id = "bot"
        entry_point = "command"

    async def fake_refund(user_id, bot_id, amount, *, vip_tier=None, reason=""):
        # 钱包调用时终态 UPDATE 应已发生,且尚未写 refunded=True
        assert updates, "退款前应已 CAS 终态"
        assert not any(u.get("refunded") is True for u in updates if isinstance(u, dict))
        refund_calls.append(
            {"user_id": user_id, "bot_id": bot_id, "amount": amount, "reason": reason}
        )
        return {"available": 1}

    monkeypatch.setattr(
        "RH_ComfyUI.core.billing.points_api.refund_points",
        fake_refund,
    )
    monkeypatch.setattr(
        "gsuid_core.utils.database.base_models.async_maker",
        _make_finalize_session_factory(row=_Row(), updates=updates),
    )

    asyncio.run(resume_mod._finalize_record(11, status="failed", error="x", elapsed_ms=1))
    assert len(refund_calls) == 1
    assert refund_calls[0]["amount"] == 12
    assert refund_calls[0]["user_id"] == "u1"
    assert len(updates) >= 2, "应有终态 UPDATE + refunded UPDATE"
    assert any(u.get("refunded") is True for u in updates if isinstance(u, dict))


def test_resume_finalize_skips_non_running(monkeypatch):
    """已终态行:不覆盖、不退款。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    refund_calls: list[dict] = []
    updates: list = []

    class _Row:
        status = "ok"
        refunded = False
        point_cost = 12
        user_id = "u1"
        bot_id = "bot"
        entry_point = "command"

    async def fake_refund(*a, **k):
        refund_calls.append({})
        return {}

    monkeypatch.setattr(
        "RH_ComfyUI.core.billing.points_api.refund_points",
        fake_refund,
    )
    monkeypatch.setattr(
        "gsuid_core.utils.database.base_models.async_maker",
        _make_finalize_session_factory(row=_Row(), updates=updates),
    )
    asyncio.run(resume_mod._finalize_record(11, status="failed", error="x"))
    assert refund_calls == []
    assert updates == []


def test_resume_finalize_http_no_rhbind_refund(monkeypatch):
    """entry_point=http(ExternalPrepaid):只写终态,不退 RHBind。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    refund_calls: list[dict] = []
    updates: list = []

    class _Row:
        status = "running"
        refunded = False
        point_cost = 12
        user_id = "u1"
        bot_id = "bot"
        entry_point = "http"

    async def fake_refund(*a, **k):
        refund_calls.append({})
        return {}

    monkeypatch.setattr(
        "RH_ComfyUI.core.billing.points_api.refund_points",
        fake_refund,
    )
    monkeypatch.setattr(
        "gsuid_core.utils.database.base_models.async_maker",
        _make_finalize_session_factory(row=_Row(), updates=updates),
    )
    asyncio.run(resume_mod._finalize_record(11, status="failed", error="x"))
    assert refund_calls == []
    assert len(updates) == 1


def test_resume_finalize_claim_miss_no_refund(monkeypatch):
    """条件 UPDATE rowcount=0(并发已抢占):不退款。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    refund_calls: list[dict] = []

    class _Row:
        status = "running"
        refunded = False
        point_cost = 12
        user_id = "u1"
        bot_id = "bot"
        entry_point = "command"

    async def fake_refund(*a, **k):
        refund_calls.append({})
        return {}

    monkeypatch.setattr(
        "RH_ComfyUI.core.billing.points_api.refund_points",
        fake_refund,
    )
    monkeypatch.setattr(
        "gsuid_core.utils.database.base_models.async_maker",
        _make_finalize_session_factory(row=_Row(), update_rowcount=0),
    )
    asyncio.run(resume_mod._finalize_record(11, status="failed", error="x"))
    assert refund_calls == []


def test_resume_finalize_refund_fail_does_not_mark_refunded(monkeypatch):
    """钱包退款失败:已写终态但不得标 refunded=True。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    updates: list = []

    class _Row:
        status = "running"
        refunded = False
        point_cost = 12
        user_id = "u1"
        bot_id = "bot"
        entry_point = "command"

    async def boom_refund(*a, **k):
        raise RuntimeError("wallet down")

    monkeypatch.setattr(
        "RH_ComfyUI.core.billing.points_api.refund_points",
        boom_refund,
    )
    monkeypatch.setattr(
        "gsuid_core.utils.database.base_models.async_maker",
        _make_finalize_session_factory(row=_Row(), updates=updates),
    )
    asyncio.run(resume_mod._finalize_record(11, status="failed", error="x"))
    assert updates, "应已写终态"
    assert not any(u.get("refunded") is True for u in updates if isinstance(u, dict))


def test_resume_seedance_cancelled_raises_resume_cancelled(monkeypatch):
    """上游 CANCELLED → ResumeCancelledError(由 resume_poll 记 status=cancelled)。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod
    from RH_ComfyUI.utils.backends.seedance.provider import NormalizedStatus

    class _Task:
        status = NormalizedStatus.CANCELLED
        error = None
        raw = {"status": "cancelled"}
        video_url = None
        last_frame_url = None
        usage = {}
        id = "t1"

    class _Provider:
        api_key = "k"

        async def poll_until_done(self, *a, **k):
            return _Task()

    monkeypatch.setattr(
        resume_mod,
        "_resolve_seedance_channel",
        lambda channel, model: (object(), "ark"),
    )
    monkeypatch.setattr(
        resume_mod,
        "_provider_from_channel",
        lambda ch, ch_name: _Provider(),
    )

    async def _run():
        try:
            await resume_mod._resume_seedance(
                vendor_task_id="t1",
                channel="ark",
                model="seedance2",
                on_progress=None,
            )
            raise AssertionError("should raise")
        except resume_mod.ResumeCancelledError as e:
            assert "取消" in str(e)

    asyncio.run(_run())


def test_resume_poll_upstream_cancelled_finalizes_cancelled(monkeypatch):
    """resume_poll 捕获 ResumeCancelledError → finalize status=cancelled。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    finals: list[dict] = []

    async def fake_finalize(record_id, *, status, error="", elapsed_ms=0):
        finals.append({"record_id": record_id, "status": status, "error": error})

    async def boom(**kwargs):
        raise resume_mod.ResumeCancelledError("upstream cancelled")

    monkeypatch.setattr(resume_mod, "_finalize_record", fake_finalize)
    monkeypatch.setattr(resume_mod, "_infer_backend", lambda **k: "seedance")
    monkeypatch.setattr(resume_mod, "_kind_for_model", lambda m, b: "video")
    monkeypatch.setattr(resume_mod, "_resume_seedance_with_cancel", boom)

    async def _run():
        try:
            await resume_mod.resume_poll(
                model="seedance2",
                vendor_task_id="t1",
                channel="ark",
                record_id=99,
            )
            raise AssertionError("should raise")
        except resume_mod.ResumeCancelledError:
            pass

    asyncio.run(_run())
    assert finals == [
        {
            "record_id": 99,
            "status": "cancelled",
            "error": "upstream cancelled",
        }
    ]


def test_serialize_extra_params_keeps_vendor_keys_when_oversized():
    """extra_params 超长时不得截出非法 JSON,且 vendor_task_id 必保留。"""
    import json

    from RH_ComfyUI.core.dispatch.active_tasks import _serialize_extra_params_keeping_vendor

    bulky = {"vendor_task_id": "old", "blob": "x" * 3000, "other": {"a": 1}}
    out = _serialize_extra_params_keeping_vendor(
        bulky,
        vendor_task_id="cgt-keep-me",
        channel_name="ark",
    )
    parsed = json.loads(out)
    assert parsed["vendor_task_id"] == "cgt-keep-me"
    assert parsed.get("vendor_channel") == "ark"
    assert len(out) <= 2048


def test_resume_not_supported_does_not_invent_failure_or_refund(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少引用只代表无法查询，不能据此把旧订单失败并退费。"""
    from RH_ComfyUI.core.dispatch import resume as resume_mod

    calls: list[dict] = []

    async def _fake_finalize(record_id, *, status, error="", elapsed_ms=0):
        calls.append(
            {
                "record_id": record_id,
                "status": status,
                "error": error,
                "elapsed_ms": elapsed_ms,
            }
        )

    monkeypatch.setattr(resume_mod, "_finalize_record", _fake_finalize)

    async def _run() -> None:
        with pytest.raises(resume_mod.ResumeNotSupportedError):
            await resume_mod.resume_poll(
                model="seedance2",
                vendor_task_id="",
                record_id=99,
            )

    asyncio.run(_run())
    assert calls == []


@pytest.mark.parametrize("failure_kind", ["network", "artifact", "unsupported"])
def test_resume_uncertain_does_not_finalize_a_record(monkeypatch: pytest.MonkeyPatch, failure_kind: str) -> None:
    import httpx

    from RH_ComfyUI.api import GenerationResult
    from RH_ComfyUI.core.dispatch import resume as resume_mod
    from RH_ComfyUI.utils.core.types import ProgressCallback

    finals: list[tuple[int | None, str]] = []

    async def capture(
        record_id: int | None,
        *,
        status: str,
        error: str = "",
        elapsed_ms: int = 0,
        actual_cost: int | None = None,
    ) -> None:
        finals.append((record_id, status))

    async def fail(
        *,
        vendor_task_id: str,
        channel: str,
        model: str,
        on_progress: ProgressCallback | None,
        trace_id: str,
        record_id: int | None,
    ) -> GenerationResult:
        if failure_kind == "network":
            raise httpx.ReadError("query response lost")
        if failure_kind == "artifact":
            raise resume_mod.ResumeFailedError("provider succeeded but artifact unavailable")
        raise resume_mod.ResumeNotSupportedError("query channel unavailable")

    monkeypatch.setattr(resume_mod, "_finalize_record", capture)
    monkeypatch.setattr(resume_mod, "_resume_seedance_with_cancel", fail)

    async def run() -> None:
        with pytest.raises((resume_mod.ResumeFailedError, resume_mod.ResumeNotSupportedError)):
            await resume_mod.resume_poll(
                model="seedance2.5",
                vendor_task_id="synthetic-reference",
                channel="synthetic-channel",
                backend="seedance",
                kind="video",
                record_id=99,
            )

    asyncio.run(run())
    assert finals == []


@pytest.mark.parametrize("family", ["seedance", "happyhorse"])
@pytest.mark.parametrize("outcome", ["failed", "cancelled", "network", "artifact", "expired"])
def test_real_provider_poll_preserves_confirmed_and_uncertain_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    family: str,
) -> None:
    import httpx

    from RH_ComfyUI.core.dispatch import resume as resume_mod
    from RH_ComfyUI.utils.backends.seedance.spec import VideoGenSpec
    from RH_ComfyUI.utils.backends.seedance.provider import NormalizedTask, NormalizedStatus, SeedanceProvider
    from RH_ComfyUI.utils.backends.happyhorse.provider import HappyHorseProvider

    class FixtureProvider(SeedanceProvider):
        name = "isolated-resume-fixture"
        poll_interval = 0.01

        async def render_create(
            self, spec: VideoGenSpec, *, model: Optional[str]
        ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
            raise AssertionError("resume must never create")

        def parse_create(self, resp_json: dict[str, Any]) -> str:
            raise AssertionError("resume must never create")

        async def get(self, task_id: str) -> NormalizedTask:
            assert task_id == "synthetic-vendor-reference"
            if outcome == "network":
                raise httpx.ReadError("synthetic query failure")
            return NormalizedTask(
                id=task_id,
                status=NormalizedStatus.SUCCEEDED if outcome == "artifact" else NormalizedStatus(outcome),
                error="synthetic confirmed status",
            )

    provider = FixtureProvider(api_key="synthetic-not-a-real-key")
    if family == "happyhorse":
        from types import MethodType

        provider.poll_until_done = MethodType(HappyHorseProvider.poll_until_done, provider)
    monkeypatch.setattr(resume_mod, "_resolve_seedance_channel", lambda channel, model: (provider, "fixture"))
    monkeypatch.setattr(resume_mod, "_provider_from_channel", lambda channel, name: provider)
    final_statuses: list[str] = []

    async def capture(
        record_id: Optional[int],
        *,
        status: str,
        error: str = "",
        elapsed_ms: int = 0,
        actual_cost: Optional[int] = None,
    ) -> None:
        final_statuses.append(status)

    monkeypatch.setattr(resume_mod, "_finalize_record", capture)

    async def run() -> None:
        with pytest.raises(resume_mod.ResumeFailedError) as failure:
            await resume_mod.resume_poll(
                model="seedance2.5",
                vendor_task_id="synthetic-vendor-reference",
                channel="fixture",
                backend="seedance",
                kind="video",
            )
        assert failure.value.definitive is (outcome in ("failed", "cancelled"))
        assert final_statuses == (
            ["cancelled" if outcome == "cancelled" else "failed"] if outcome in ("failed", "cancelled") else []
        )

    asyncio.run(run())


def test_provider_supports_remote_cancel_fail_closed():
    from RH_ComfyUI.core.dispatch.resume import _provider_supports_remote_cancel

    class _NoMethod:
        pass

    class _Yes:
        def supports_remote_cancel(self) -> bool:
            return True

    class _Boom:
        def supports_remote_cancel(self) -> bool:
            raise RuntimeError("x")

    assert _provider_supports_remote_cancel(None) is False
    assert _provider_supports_remote_cancel(_NoMethod()) is False
    assert _provider_supports_remote_cancel(_Yes()) is True
    assert _provider_supports_remote_cancel(_Boom()) is False


def test_best_effort_skips_when_remote_already_attempted():
    """cancel_generation 已尝试上游后,CancelledError 兜底不再二次 DELETE。"""

    async def _run() -> None:
        reg = get_active_task_registry()
        # 清空可能残留
        for ag in list(reg._by_trace.values()):
            await reg.unregister(ag)

        async def _worker() -> None:
            await asyncio.sleep(60)

        task = asyncio.create_task(_worker())
        ag = await reg.register(
            model_name="seedance2",
            trace_id="trace-double-del",
            task=task,
        )
        remote = AsyncMock()
        await reg.bind_vendor_cancel(
            vendor_task_id="cgt-dd",
            cancel_remote=remote,
            channel_name="ark",
            ag=ag,
        )
        # 模拟 cancel_generation 已 remote
        ag.remote_cancel_attempted = True
        from RH_ComfyUI.core.dispatch.active_tasks import remote_cancel_already_attempted

        # current() 绑定在 worker 任务上,不在本协程;直接断言 flag + 函数语义
        assert ag.remote_cancel_attempted is True
        # 把 current task 指到 ag 的 task,再在该 task 内检查
        checked: list[bool] = []

        async def _inside() -> None:
            # re-register local map for this task
            await reg.unregister(ag)
            ag.task = asyncio.current_task()
            await reg.register(
                model_name="seedance2",
                trace_id="trace-double-del-2",
                task=ag.task,
            )
            cur = reg.current()
            assert cur is not None
            cur.remote_cancel_attempted = True
            checked.append(remote_cancel_already_attempted())

        await _inside()
        assert checked == [True]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


def test_rh_app_cannot_cancel_only_resume():
    """rh_app:落库 task_id 供 resume;cancel_generation 一律拒绝。"""

    async def _run() -> None:
        reg = ActiveTaskRegistry()
        ag = await reg.register(
            model_name="anima",
            trace_id="rh-t",
            record_id=3,
            allow_cancel=False,
        )
        await reg.bind_vendor_task(
            vendor_task_id="rh-task-1",
            channel_name="rh_app",
            cancel_remote=None,
            ag=ag,
        )
        assert ag.vendor_task_id == "rh-task-1"
        assert ag.cancel_remote is None
        assert ag.allow_cancel is False

        task = asyncio.create_task(asyncio.sleep(30))
        ag.task = task
        out = await reg.cancel(trace_id="rh-t")
        assert out["found"] is True
        assert out["ok"] is False
        assert out["cancelled_local"] is False
        assert out["cancelled_remote"] is False
        assert "不支持取消" in out["message"]
        # 任务仍在跑
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


def test_mark_last_failed_refunded_accepts_cancelled():
    """cancelled 终态也可 mark refunded(PointsBillingPolicy post_refund)。"""
    import inspect

    from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord

    src = inspect.getsource(RHComfyuiTaskRecord.mark_last_failed_refunded)
    assert "CANCELLED" in src


def test_mark_host_wallet_refunded_running_to_cancelled():
    """宿主 cancel:running + trace → cancelled + refunded。"""
    from RH_ComfyUI.utils.database.models import RHComfyuiTaskRecord

    class _Row:
        status = "running"
        refunded = False

    row = _Row()

    class _Result:
        def scalar_one_or_none(self):
            return row

    class _Sess:
        async def execute(self, stmt):
            return _Result()

        def add(self, r):
            pass

    # with_session 包装:__wrapped__(cls, session, ...)
    inner = RHComfyuiTaskRecord.mark_host_wallet_refunded.__wrapped__  # type: ignore[attr-defined]
    ok = asyncio.run(
        inner(
            RHComfyuiTaskRecord,
            _Sess(),
            trace_id="job-1",
            record_id=None,
            terminal_status="cancelled",
        )
    )
    assert ok is True
    assert row.status == "cancelled"
    assert row.refunded is True
