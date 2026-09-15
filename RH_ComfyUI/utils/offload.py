"""生成路径 CPU 卸载:大 base64 / 编解码离开事件循环。"""

from __future__ import annotations

import base64
import asyncio
from typing import TypeVar, Callable

OFFLOAD_MIN_BYTES = 256 * 1024

_T = TypeVar("_T")


def b64ascii(data: bytes) -> str:
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")


async def b64ascii_async(data: bytes) -> str:
    if not data:
        return ""
    if len(data) < OFFLOAD_MIN_BYTES:
        return b64ascii(data)
    return await asyncio.to_thread(b64ascii, data)


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{b64ascii(data)}"


async def data_url_async(data: bytes, mime: str) -> str:
    if len(data) < OFFLOAD_MIN_BYTES:
        return data_url(data, mime)
    return await asyncio.to_thread(data_url, data, mime)


async def to_thread_if_large(size: int, fn: Callable[..., _T], *args: object) -> _T:
    if size < OFFLOAD_MIN_BYTES:
        return fn(*args)
    return await asyncio.to_thread(fn, *args)
