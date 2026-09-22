"""厂商创建请求前的两道闸。

要求落账的调用方必须先提交消费行，才进入 ``model.run``。
真正调用 ``execute_on_channel`` 之前可通知宿主钩子；钩子失败必须中止。
"""

from __future__ import annotations

from typing import Callable, Awaitable
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator

VendorCreateHook = Callable[[], Awaitable[None]]

_REQUIRE_TASK_RECORD: ContextVar[bool] = ContextVar("rh_require_task_record", default=False)
_VENDOR_CREATE_HOOK: ContextVar[VendorCreateHook | None] = ContextVar("rh_vendor_create_hook", default=None)


def is_task_record_required() -> bool:
    return _REQUIRE_TASK_RECORD.get()


@contextmanager
def task_record_required_scope(enabled: bool) -> Iterator[None]:
    token = _REQUIRE_TASK_RECORD.set(bool(enabled) or _REQUIRE_TASK_RECORD.get())
    try:
        yield
    finally:
        _REQUIRE_TASK_RECORD.reset(token)


@contextmanager
def vendor_create_hook_scope(hook: VendorCreateHook | None) -> Iterator[None]:
    token = _VENDOR_CREATE_HOOK.set(hook)
    try:
        yield
    finally:
        _VENDOR_CREATE_HOOK.reset(token)


async def notify_vendor_create_starting() -> None:
    """进入厂商执行前调用。无钩子则直接返回。"""
    hook = _VENDOR_CREATE_HOOK.get()
    if hook is not None:
        await hook()


__all__ = [
    "VendorCreateHook",
    "is_task_record_required",
    "notify_vendor_create_starting",
    "task_record_required_scope",
    "vendor_create_hook_scope",
]
