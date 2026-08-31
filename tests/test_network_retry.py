"""Transport-layer 5× / 5s retry: network blips wait; HTTP status does not."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from RH_ComfyUI.utils.backends.http_retry import (
    NETWORK_RETRY_ATTEMPTS,
    RetryingAsyncClient,
    is_network_error,
    call_with_network_retry,
)


class _FailNThenOk(httpx.AsyncBaseTransport):
    def __init__(self, fails: int, then_status: int = 200) -> None:
        self.fails = fails
        self.then_status = then_status
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fails:
            raise httpx.ReadError("", request=request)
        return httpx.Response(self.then_status, json={"ok": True})


class _AlwaysStatus(httpx.AsyncBaseTransport):
    def __init__(self, status: int) -> None:
        self.status = status
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self.status, json={"error": "no"})


def test_real_submit_opt_in_reaches_transport_and_posts_once() -> None:
    from uuid import uuid4

    from RH_ComfyUI.api import submit
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.core.schema.card import ModelCard
    from RH_ComfyUI.core.schema.types import PortSpec, PortType, NodeOutput, ProgressCallback
    from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
    from RH_ComfyUI.core.base.generation import AIGCGenerationBase
    from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding
    from RH_ComfyUI.core.routing.registry import model_registry

    transport = _FailNThenOk(fails=10)
    model_name = "strict_transport_" + uuid4().hex

    class FixtureModel(AIGCGenerationBase):
        name = model_name
        display_name = model_name
        modality = TaskType.IMAGE
        point_cost = 0
        card = ModelCard(description="Synthetic transport fixture")

        def input_schema(self) -> dict[str, PortSpec]:
            return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

        def channel_bindings(self) -> list[ChannelBinding]:
            return [ChannelBinding(LocalChannel("synthetic-transport"))]

        async def execute_on_channel(
            self,
            request: GenerationRequest,
            binding: ChannelBinding,
            *,
            on_progress: ProgressCallback | None = None,
        ) -> NodeOutput:
            async with RetryingAsyncClient(transport=transport, retry_wait_s=0) as client:
                await client.post("https://isolated.test/create", json={"synthetic": True})
            raise AssertionError("transport must fail")

    async def run() -> None:
        await base_models.init_database()
        model_registry.register(FixtureModel())
        with pytest.raises(httpx.ReadError):
            await submit(
                model=model_name, prompt="synthetic", task_type="image", strict_create_once=True, trace_id=model_name
            )
        assert transport.calls == 1
        await base_models.engine.dispose()

    asyncio.run(run())


def test_retry_then_success():
    transport = _FailNThenOk(fails=2)

    async def _run() -> None:
        async with RetryingAsyncClient(transport=transport, retry_wait_s=0) as client:
            resp = await client.get("https://example.test/poll")
        assert resp.status_code == 200
        assert transport.calls == 3

    asyncio.run(_run())


def test_five_failures_raise_last_error():
    transport = _FailNThenOk(fails=10)

    async def _run() -> None:
        async with RetryingAsyncClient(transport=transport, retry_wait_s=0) as client:
            with pytest.raises(httpx.ReadError):
                await client.get("https://example.test/poll")
        assert transport.calls == NETWORK_RETRY_ATTEMPTS

    asyncio.run(_run())


def test_http_400_is_not_retried():
    transport = _AlwaysStatus(400)

    async def _run() -> None:
        async with RetryingAsyncClient(transport=transport, retry_wait_s=0) as client:
            resp = await client.get("https://example.test/create")
        assert resp.status_code == 400
        assert transport.calls == 1

    asyncio.run(_run())


def test_value_error_is_not_retried():
    n = {"calls": 0}

    async def _op() -> int:
        n["calls"] += 1
        raise ValueError("business")

    async def _run() -> None:
        with pytest.raises(ValueError, match="business"):
            await call_with_network_retry(_op, wait_s=0)
        assert n["calls"] == 1

    asyncio.run(_run())


def test_is_network_error():
    req = httpx.Request("GET", "https://example.test/")
    assert is_network_error(httpx.ReadError("", request=req))
    assert is_network_error(httpx.ConnectError("boom", request=req))
    assert not is_network_error(ValueError("no"))
    assert not is_network_error(httpx.HTTPStatusError("400", request=req, response=httpx.Response(400)))


def test_strict_create_once_does_not_retry_post_but_keeps_query_retries() -> None:
    from RH_ComfyUI.utils.backends.http_retry import strict_create_once_scope

    async def run() -> None:
        post_transport = _FailNThenOk(fails=20)
        async with RetryingAsyncClient(transport=post_transport, retry_wait_s=0) as client:
            with strict_create_once_scope(True), pytest.raises(httpx.ReadError):
                await client.post("https://example.test/create", json={"request": "one"})
        assert post_transport.calls == 1
        get_transport = _FailNThenOk(fails=2)
        async with RetryingAsyncClient(transport=get_transport, retry_wait_s=0) as client:
            with strict_create_once_scope(True):
                response = await client.get("https://example.test/query")
        assert response.status_code == 200 and get_transport.calls == 3

    asyncio.run(run())


def test_strict_mode_is_request_scoped_not_a_shared_client_mutation() -> None:
    from RH_ComfyUI.utils.backends.http_retry import is_strict_create_once, strict_create_once_scope

    calls: dict[str, int] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] = calls[path] + 1 if path in calls else 1
        if calls[path] == 1:
            raise httpx.ReadError("ack lost", request=request)
        return httpx.Response(200, json={"ok": True})

    async def run() -> None:
        async with RetryingAsyncClient(transport=httpx.MockTransport(handler), retry_wait_s=0) as client:

            async def strict_call() -> None:
                with strict_create_once_scope(True), pytest.raises(httpx.ReadError):
                    await client.post("https://example.test/strict")

            async def legacy_call() -> None:
                assert not is_strict_create_once()
                assert (await client.post("https://example.test/legacy")).status_code == 200

            await asyncio.gather(strict_call(), legacy_call())
        assert calls == {"/strict": 1, "/legacy": 2}
        assert not is_strict_create_once()

    asyncio.run(run())


def test_seedance_request_retries_readerror(monkeypatch):
    from RH_ComfyUI.utils.backends.seedance.provider import SeedanceProvider

    class _P(SeedanceProvider):
        name = "t"
        DEFAULT_BASE_URL = "https://example.test"

        async def render_create(self, spec, *, model):  # pragma: no cover
            return "POST", self.base_url, {}, {}

        def parse_create(self, resp_json):  # pragma: no cover
            return "id"

        async def get(self, task_id):  # pragma: no cover
            raise NotImplementedError

    monkeypatch.setattr(
        "RH_ComfyUI.rh_config.comfyui_config.plugin_dry_run",
        lambda: False,
    )
    transport = _FailNThenOk(fails=2)
    p = _P(api_key="k", dry_run=False)
    p._client = RetryingAsyncClient(transport=transport, retry_wait_s=0)

    async def _run() -> None:
        body = await p._request("GET", "https://example.test/tasks/1")
        assert body == {"ok": True}
        assert transport.calls == 3

    asyncio.run(_run())


def test_seedance_h3_clients_are_retrying():
    from RH_ComfyUI.utils.backends.seedance.provider import SeedanceProvider
    from RH_ComfyUI.utils.backends.minimax.h3_provider import MiniMaxH3Provider

    class _P(SeedanceProvider):
        name = "t"
        DEFAULT_BASE_URL = "https://example.test"

        async def render_create(self, spec, *, model):  # pragma: no cover
            return "POST", self.base_url, {}, {}

        def parse_create(self, resp_json):  # pragma: no cover
            return "id"

        async def get(self, task_id):  # pragma: no cover
            raise NotImplementedError

    p = _P(api_key="k")
    assert isinstance(p._get_client(), RetryingAsyncClient)
    h3 = MiniMaxH3Provider(api_key="k")
    assert isinstance(h3._get_client(), RetryingAsyncClient)


def test_sleeps_between_attempts(monkeypatch):
    sleeps: list[float] = []

    async def _fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    n = {"calls": 0}

    async def _op() -> int:
        n["calls"] += 1
        raise httpx.ReadError("", request=httpx.Request("GET", "https://example.test/"))

    async def _run() -> None:
        with pytest.raises(httpx.ReadError):
            await call_with_network_retry(_op, attempts=5, wait_s=5.0)

    asyncio.run(_run())
    assert n["calls"] == 5
    assert sleeps == [5.0, 5.0, 5.0, 5.0]
