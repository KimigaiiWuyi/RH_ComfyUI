"""Upstream HTTP transport retry.

Network blips (ReadError / ConnectError / timeout) wait then retry the same
request. HTTP 4xx/5xx *responses* are not retried here — they already have a
status. After all attempts fail, the last exception propagates so the caller
can surface a network error (e.g. POLL_NETWORK_ERROR).

Do not wrap this helper around a poll loop that already retries the same GET:
one layer of 5× / 5s is the contract.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Iterator, Awaitable

import httpx

from gsuid_core.logger import logger

T = TypeVar("T")

NETWORK_RETRY_ATTEMPTS = 5
NETWORK_RETRY_WAIT_S = 5.0
_STRICT_CREATE_ONCE: ContextVar[bool] = ContextVar("rh_strict_create_once", default=False)


def is_strict_create_once() -> bool:
    return _STRICT_CREATE_ONCE.get()


@contextmanager
def strict_create_once_scope(enabled: bool) -> Iterator[None]:
    token = _STRICT_CREATE_ONCE.set(enabled or _STRICT_CREATE_ONCE.get())
    try:
        yield
    finally:
        _STRICT_CREATE_ONCE.reset(token)


def _aiohttp_network_exceptions() -> tuple[type[BaseException], ...]:
    try:
        import aiohttp
    except ImportError:
        return ()
    # ClientResponseError (HTTP status) is *not* included — that is not a blip.
    return (
        aiohttp.ClientConnectionError,
        aiohttp.ServerTimeoutError,
        aiohttp.ClientPayloadError,
    )


# TransportError covers ReadError / ConnectError / TimeoutException / ProtocolError.
# TimeoutError / OSError cover asyncio.wait_for and socket-level failures.
# aiohttp connection/payload errors are the equivalent for leftover aiohttp clients.
NETWORK_RETRY_EXC: tuple[type[BaseException], ...] = (
    httpx.TransportError,
    asyncio.TimeoutError,
    OSError,
    *_aiohttp_network_exceptions(),
)


def is_network_error(exc: BaseException) -> bool:
    """True for transport / socket blips; False for HTTP status and business errors."""
    return isinstance(exc, NETWORK_RETRY_EXC)


async def call_with_network_retry(
    op: Callable[[], Awaitable[T]],
    *,
    attempts: int = NETWORK_RETRY_ATTEMPTS,
    wait_s: float = NETWORK_RETRY_WAIT_S,
    label: str = "",
) -> T:
    """Run ``op`` up to ``attempts`` times; wait ``wait_s`` between network failures."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last: BaseException | None = None
    prefix = f"[network-retry] {label}" if label else "[network-retry]"
    for i in range(1, attempts + 1):
        try:
            return await op()
        except NETWORK_RETRY_EXC as exc:
            last = exc
            if i >= attempts:
                logger.warning(f"{prefix} {type(exc).__name__}: {exc}; {i}/{attempts} 次均失败,向上抛出")
                break
            logger.warning(f"{prefix} {type(exc).__name__}: {exc}; {i}/{attempts} 次失败, {wait_s:.0f}s 后重试")
            await asyncio.sleep(wait_s)
    assert last is not None
    raise last


class RetryingAsyncClient(httpx.AsyncClient):
    """httpx client whose ``request()`` retries transport errors 5× / 5s.

    ``get`` / ``post`` / ``delete`` all go through ``request()``, so poll GET and
    create POST share the same policy. HTTP status codes are returned as-is.
    """

    def __init__(
        self,
        *args: Any,
        retry_attempts: int = NETWORK_RETRY_ATTEMPTS,
        retry_wait_s: float = NETWORK_RETRY_WAIT_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._retry_attempts = retry_attempts
        self._retry_wait_s = retry_wait_s

    async def request(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        send = super().request

        async def _once() -> httpx.Response:
            return await send(method, url, **kwargs)

        return await call_with_network_retry(
            _once,
            attempts=1
            if is_strict_create_once() and method.upper() not in ("GET", "HEAD", "OPTIONS")
            else self._retry_attempts,
            wait_s=self._retry_wait_s,
            label=f"{method} {url}",
        )


async def download_with_network_retry(
    url: str,
    *,
    timeout: float = 300.0,
    attempts: int = NETWORK_RETRY_ATTEMPTS,
    wait_s: float = NETWORK_RETRY_WAIT_S,
    label: str = "",
) -> bytes:
    """GET a URL; retry transport errors and HTTP 5xx. Immediate raise on HTTP 4xx.

    Uses a plain ``AsyncClient`` (not ``RetryingAsyncClient``) so the 5× loop
    here is the only retry layer. 5xx is included because CDN/temp links flake
    the same way as a dropped socket.
    """
    last_exc: BaseException | None = None
    tag = label or url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for i in range(1, attempts + 1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise
                last_exc = exc
            except NETWORK_RETRY_EXC as exc:
                last_exc = exc
            if i < attempts:
                logger.warning(
                    f"[network-retry] GET {tag} {type(last_exc).__name__}: {last_exc}; "
                    f"{i}/{attempts} 次失败, {wait_s:.0f}s 后重试"
                )
                await asyncio.sleep(wait_s)
    assert last_exc is not None
    raise last_exc


__all__ = [
    "is_strict_create_once",
    "strict_create_once_scope",
    "NETWORK_RETRY_ATTEMPTS",
    "NETWORK_RETRY_EXC",
    "NETWORK_RETRY_WAIT_S",
    "RetryingAsyncClient",
    "call_with_network_retry",
    "download_with_network_retry",
    "is_network_error",
]
