"""钱包回执事务：临时 SQLite 沙箱，不碰生产 GsData.db。"""

from __future__ import annotations

import os
import json
import asyncio
import multiprocessing
from uuid import uuid4
from typing import TYPE_CHECKING, Literal
from pathlib import Path
from functools import wraps
from dataclasses import replace
from collections.abc import Callable, Awaitable

import pytest
from sqlmodel import col
from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from multiprocessing.queues import Queue
    from multiprocessing.synchronize import Lock, Event, Barrier

    from RH_ComfyUI.core.billing.points_api import WalletOperationCommand

_SANDBOX_ENV = "RH_COMFYUI_WALLET_TEST_DB"


def _aio(fn: Callable[..., Awaitable[None]]) -> Callable[..., None]:
    @wraps(fn)
    def wrapped(*args: object, **kwargs: object) -> None:
        asyncio.run(fn(*args, **kwargs))

    return wrapped


def wallet_sandbox_path() -> Path:
    raw = os.environ.get(_SANDBOX_ENV, "")
    if not raw:
        raise RuntimeError("wallet tests require a temp sqlite path")
    return Path(raw).resolve()


def _live_sqlite_path() -> Path:
    from gsuid_core.data_store import get_res_path

    return (get_res_path() / "GsData.db").resolve()


def _refuse_live_sqlite(path: Path) -> Path:
    resolved = path.resolve()
    live_dir = _live_sqlite_path().parent
    if resolved.name == "GsData.db" or resolved == _live_sqlite_path() or resolved.is_relative_to(live_dir):
        raise RuntimeError("wallet tests refuse the live Core data directory")
    return resolved


def bound_sqlite_path() -> Path:
    from gsuid_core.utils.database import base_models

    raw = base_models.engine.url.database
    if not raw:
        raise RuntimeError("wallet sandbox engine has no sqlite path")
    return Path(raw).resolve()


async def attach_wallet_sandbox(path: Path) -> Path:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database import models

    resolved = _refuse_live_sqlite(path)
    current = base_models.engine
    if current not in (None, "") and base_models._db_initialized:
        database = current.url.database
        if database and Path(database).resolve() == resolved:
            return resolved
        await current.dispose()
    base_models._db_initialized = False
    base_models.db_url = str(resolved)
    await base_models.init_database()
    if bound_sqlite_path() != resolved:
        raise RuntimeError("wallet sandbox engine bound the wrong sqlite file")
    async with base_models.engine.begin() as connection:
        await connection.run_sync(models.RHBind.metadata.create_all)
        await connection.run_sync(models.RHWalletOperation.metadata.create_all)
        for statement in models.WALLET_OPERATION_MIGRATIONS:
            await connection.execute(text(statement))
    return resolved


@pytest.fixture(autouse=True)
def wallet_sandbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.getbasetemp() / "rh-wallet-sandbox.sqlite"
    os.environ[_SANDBOX_ENV] = str(path.resolve())
    return asyncio.run(attach_wallet_sandbox(path))


@_aio
async def test_sandbox_is_not_the_live_database_and_rolls_back(wallet_sandbox: Path) -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database import models

    bound = bound_sqlite_path()
    assert bound == wallet_sandbox.resolve()
    assert bound != _live_sqlite_path()
    async with base_models.async_maker() as session:
        session.add(models.RHBind(user_id="isolation-rollback-fixture", bot_id="fixture"))
        await session.flush()
        await session.rollback()
    async with base_models.async_maker() as session:
        found = await session.execute(
            select(models.RHBind).where(col(models.RHBind.user_id) == "isolation-rollback-fixture")
        )
        assert found.scalar_one_or_none() is None


@_aio
async def test_charge_has_durable_receipt_and_replays_once() -> None:
    from RH_ComfyUI.utils.database.models import RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import WalletOperationCommand, charge_points_once

    key = "receipt-" + uuid4().hex
    command = WalletOperationCommand(
        operation_key=f"{key}:charge:v1",
        job_key=key,
        external_ref=key,
        kind="charge",
        user_id=key,
        bot_id="fixture",
        request_hash="a" * 64,
        price_revision="fixture-v1",
        requested_target_points=100,
    )
    first = await charge_points_once(command)
    replay = await charge_points_once(command)
    assert first.status == "committed"
    assert first.billed_delta_points == first.net_after_points == 100
    assert replay.receipt_digest == first.receipt_digest
    stored = await RHWalletOperation.get_operation(command.operation_key)
    assert stored is not None
    assert stored.receipt_digest == first.receipt_digest


@_aio
async def test_wallet_and_receipt_share_the_caller_transaction() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import WalletOperationCommand, charge_points_in_session

    key = "rollback-" + uuid4().hex
    command = WalletOperationCommand(
        operation_key=f"{key}:charge:v1",
        job_key=key,
        external_ref=key,
        kind="charge",
        user_id=key,
        bot_id="fixture",
        request_hash="b" * 64,
        price_revision="fixture-v1",
        requested_target_points=100,
    )
    async with base_models.async_maker() as session:
        await session.execute(text("BEGIN IMMEDIATE"))
        await charge_points_in_session(session, command)
        await session.rollback()
    assert await RHWalletOperation.get_operation(command.operation_key) is None
    assert await RHBind.select_data(user_id=key, bot_id="fixture") is None


@_aio
async def test_legacy_refund_never_reduces_a_downgraded_bucket() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind

    key = "downgrade-" + uuid4().hex
    async with base_models.async_maker() as session:
        session.add(
            RHBind(
                user_id=key,
                bot_id="fixture",
                vip_tier="free",
                point=100_000,
                point_5h=100_000,
                point_day=100_000,
                point_week=100_000,
            )
        )
        await session.commit()
    status = await RHBind.add_triple(key, "fixture", 10)
    assert all(bucket["balance"] >= 100_000 for bucket in status["buckets"].values())


@_aio
async def test_duplicate_wallet_is_rejected_before_any_mutation() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind

    key = "duplicate-" + uuid4().hex
    async with base_models.async_maker() as session:
        for _ in range(2):
            session.add(RHBind(user_id=key, bot_id="fixture", point_5h=500, point_day=500, point_week=500))
        await session.commit()
    with pytest.raises(Exception, match="WALLET_BINDING_NOT_UNIQUE"):
        await RHBind.deduct_triple(key, "fixture", 10)
    async with base_models.async_maker() as session:
        rows = (await session.execute(select(RHBind).where(col(RHBind.user_id) == key))).scalars().all()
        assert len(rows) == 2
        assert all(row.point_5h == row.point_day == row.point_week == 500 for row in rows)


def operation_command(
    key: str,
    kind: Literal["charge", "settle", "refund"] = "charge",
    points: int = 100,
    *,
    user_id: str | None = None,
    vip_tier: str | None = None,
) -> WalletOperationCommand:
    from RH_ComfyUI.core.billing.points_api import WalletOperationCommand

    return WalletOperationCommand(
        operation_key=f"{key}:{kind}:v1",
        job_key=key,
        external_ref=key,
        kind=kind,
        user_id=user_id or key,
        bot_id="fixture",
        request_hash="a" * 64,
        price_revision="fixture-v1",
        requested_target_points=points,
        vip_tier=vip_tier,
    )


@pytest.mark.parametrize("bad", [True, False, 0.5, float("nan"), float("inf"), -1, 2_000_000_001, "100"])
@_aio
async def test_rejects_non_integer_or_out_of_range_commands(bad: int | float | str) -> None:
    with pytest.raises(ValueError, match="bounded nonnegative integer"):
        operation_command("invalid-points", points=bad)


@_aio
async def test_operation_and_context_conflicts_are_not_recharges() -> None:
    from RH_ComfyUI.utils.database.models import RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import WalletOperationConflict, charge_points_once

    key = "conflicts-" + uuid4().hex
    command = operation_command(key)
    first = await charge_points_once(command)
    for changed in (
        replace(command, requested_target_points=101),
        replace(command, user_id="someone-else"),
        replace(command, bot_id="another-pool"),
        replace(command, request_hash="b" * 64),
        replace(command, external_ref="another-ref"),
        replace(command, price_revision="another-price"),
        replace(command, operation_key=command.operation_key + "-other"),
    ):
        with pytest.raises(WalletOperationConflict):
            await charge_points_once(changed)
    operations = await RHWalletOperation.get_job_operations(key)
    assert len(operations) == 1
    assert operations[0].receipt_digest == first.receipt_digest


@pytest.mark.parametrize("vip_tier", [None, "unlimited"])
@pytest.mark.parametrize("settle_target", [50, 150])
@_aio
async def test_charge_settle_refund_preserves_exact_net(
    vip_tier: str | None,
    settle_target: int,
) -> None:
    from RH_ComfyUI.utils.database.models import RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import charge_points_once, refund_points_once, settle_points_once

    key = "lifecycle-" + uuid4().hex
    charge = await charge_points_once(operation_command(key, vip_tier=vip_tier))
    settle = await settle_points_once(operation_command(key, "settle", settle_target))
    refund_command = operation_command(key, "refund", 0)
    refund = await refund_points_once(refund_command)
    replay = await refund_points_once(refund_command)
    assert (charge.billed_delta_points, settle.billed_delta_points, refund.billed_delta_points) == (
        100,
        settle_target - 100,
        -settle_target,
    )
    assert refund.net_after_points == 0
    assert refund.predecessor_operation_keys == [charge.operation_key, settle.operation_key]
    assert refund.receipt_digest == replay.receipt_digest
    operations = await RHWalletOperation.get_job_operations(key)
    assert len(operations) == 3
    assert sum(op.billed_delta_points for op in operations) == 0
    if vip_tier == "unlimited":
        assert all(op.quota_mode == "unlimited" for op in operations)
        assert all(op.bucket_before == op.bucket_after for op in operations)


@_aio
async def test_refund_wins_over_a_late_settlement() -> None:
    from RH_ComfyUI.utils.database.models import RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import charge_points_once, refund_points_once, settle_points_once

    key = "refund-first-" + uuid4().hex
    await charge_points_once(operation_command(key))
    refund = await refund_points_once(operation_command(key, "refund", 0))
    settle = await settle_points_once(operation_command(key, "settle", 150))
    assert settle.status == "declined"
    assert settle.reason == "already_refunded"
    assert settle.billed_delta_points == settle.net_after_points == 0
    assert refund.billed_delta_points == -100
    assert sum(op.billed_delta_points for op in await RHWalletOperation.get_job_operations(key)) == 0


@_aio
async def test_declined_topup_is_not_paid_or_automatically_retried() -> None:
    from RH_ComfyUI.utils.database.models import RHBind
    from RH_ComfyUI.core.billing.points_api import charge_points_once, settle_points_once

    key = "declined-" + uuid4().hex
    await charge_points_once(operation_command(key))
    command = operation_command(key, "settle", 9000)
    first = await settle_points_once(command)
    assert first.status == "declined"
    assert first.billed_delta_points == 0
    assert first.net_after_points == 100
    await RHBind.set_vip_tier(key, "fixture", "basic")
    replay = await settle_points_once(command)
    assert replay.receipt_digest == first.receipt_digest
    assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 20000


@_aio
async def test_no_committed_charge_means_no_refund() -> None:
    from RH_ComfyUI.utils.database.models import RHBind
    from RH_ComfyUI.core.billing.points_api import WalletOperationConflict, refund_points_once

    key = "no-charge-" + uuid4().hex
    with pytest.raises(WalletOperationConflict, match="COMMITTED_CHARGE_REQUIRED"):
        await refund_points_once(operation_command(key, "refund", 0))
    assert await RHBind.select_data(user_id=key, bot_id="fixture") is None


@_aio
async def test_zero_legacy_refund_does_not_refresh_expired_buckets() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind
    from RH_ComfyUI.core.billing.points_api import refund_points

    key = "zero-refund-" + uuid4().hex
    async with base_models.async_maker() as session:
        session.add(
            RHBind(
                user_id=key,
                bot_id="fixture",
                point=17,
                point_5h=17,
                point_day=19,
                point_week=23,
                refreshed_at_5h=1,
                refreshed_at_day=1,
                refreshed_at_week=1,
            )
        )
        await session.commit()
    status = await refund_points(key, "fixture", 0)
    assert status["available"] == 17
    assert status["refreshed_at"] == {"h5": 1, "day": 1, "week": 1}


@pytest.mark.parametrize("stage", ["after_wallet_update", "before_receipt_insert", "after_receipt_insert"])
@_aio
async def test_real_sql_fault_rolls_back_wallet_and_receipt(stage: str) -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import charge_points_once

    key = "fault-" + uuid4().hex
    await RHBind.create_data(key, "fixture")
    trigger = "wallet_fault_" + uuid4().hex
    event = {
        "after_wallet_update": "AFTER UPDATE ON rhbind",
        "before_receipt_insert": "BEFORE INSERT ON rhwalletoperation",
        "after_receipt_insert": "AFTER INSERT ON rhwalletoperation",
    }[stage]
    async with base_models.engine.begin() as connection:
        await connection.execute(
            text(
                f"CREATE TRIGGER {trigger} {event} WHEN NEW.user_id = '{key}' "
                "BEGIN SELECT RAISE(ABORT, 'INJECTED_WALLET_FAULT'); END"
            )
        )
    try:
        with pytest.raises(IntegrityError, match="INJECTED_WALLET_FAULT"):
            await charge_points_once(operation_command(key))
        assert await RHWalletOperation.get_operation(f"{key}:charge:v1") is None
        row = await RHBind.select_data(user_id=key, bot_id="fixture")
        assert row is not None
        assert (row.point_5h, row.point_day, row.point_week) == (8000, 20000, 80000)
    finally:
        async with base_models.engine.begin() as connection:
            await connection.execute(text(f"DROP TRIGGER {trigger}"))


@_aio
async def test_receipt_migration_is_repeatable_and_receipts_are_immutable() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import WALLET_OPERATION_MIGRATIONS, RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import charge_points_once

    key = "immutable-" + uuid4().hex
    receipt = await charge_points_once(operation_command(key))
    for _ in range(2):
        async with base_models.engine.begin() as connection:
            for statement in WALLET_OPERATION_MIGRATIONS:
                await connection.execute(text(statement))
    async with base_models.async_maker() as session:
        primary_keys = await session.execute(text("PRAGMA table_info(rhbind)"))
        assert [row[1] for row in primary_keys if row[5]] == ["id"]
    for statement in (
        "UPDATE rhwalletoperation SET billed_delta_points=99 WHERE operation_key=:key",
        "DELETE FROM rhwalletoperation WHERE operation_key=:key",
    ):
        async with base_models.async_maker() as session:
            with pytest.raises(IntegrityError, match="WALLET_RECEIPT_IMMUTABLE"):
                await session.execute(text(statement), {"key": receipt.operation_key})
            await session.rollback()
    stored = await RHWalletOperation.get_operation(receipt.operation_key)
    assert stored is not None
    assert stored.receipt_digest == receipt.receipt_digest


def _process_wallet_requests(
    user_id: str,
    job_prefix: str,
    mode: str,
    count: int,
    barrier: Barrier,
    output: Queue,
    import_lock: Lock,
) -> None:
    try:
        from pytest_socket import disable_socket

        disable_socket(allow_unix_socket=True)
    except ImportError:
        pass

    async def run() -> list[str]:
        # Core 导入会更新合成配置的 .part 文件；只串行导入，不串行钱包请求。
        try:
            with import_lock:
                from gsuid_core.utils.database import base_models
                from RH_ComfyUI.utils.database.models import RHBind
                from RH_ComfyUI.core.billing.points_api import (
                    charge_points_once,
                    refund_points_once,
                    settle_points_once,
                )

                await attach_wallet_sandbox(wallet_sandbox_path())
        except Exception:
            barrier.abort()
            raise
        await asyncio.to_thread(barrier.wait, 40)

        async def request(index: int) -> str:
            if mode == "quota":
                return str((await RHBind.get_quota_status(user_id, "fixture"))["available"])
            if mode == "create":
                return str((await RHBind.create_data(user_id, "fixture")).point)
            if mode == "tier":
                return str((await RHBind.set_vip_tier(user_id, "fixture", "basic", refill=False))["available"])
            if mode == "refill":
                return str((await RHBind.refill_buckets(user_id, "fixture", "h5"))["available"])
            if mode == "add":
                return str((await RHBind.add_triple(user_id, "fixture", 10))["available"])
            if mode == "deduct":
                return str((await RHBind.deduct_triple(user_id, "fixture", 10))[0])
            if mode == "refund":
                op = await refund_points_once(operation_command(job_prefix, "refund", 0, user_id=user_id))
            elif mode == "settle":
                op = await settle_points_once(operation_command(job_prefix, "settle", 150, user_id=user_id))
            else:
                key = f"{job_prefix}-{index}" if mode == "different" else job_prefix
                from RH_ComfyUI.core.billing.points_api import WalletOperationConflict

                command = operation_command(key, points=10 if mode == "different" else 100, user_id=user_id)
                if mode == "changed_amount":
                    command = replace(command, requested_target_points=101)
                elif mode == "changed_actor":
                    command = replace(command, user_id=user_id + "-other")
                elif mode == "changed_hash":
                    command = replace(command, request_hash="b" * 64)
                try:
                    op = await charge_points_once(command)
                except WalletOperationConflict:
                    return "conflict"
            return op.receipt_digest

        result = await asyncio.gather(*(request(index) for index in range(count)))
        await base_models.engine.dispose()
        return result

    try:
        output.put(("ok", asyncio.run(run())))
    except Exception as exc:
        output.put(("err", f"{type(exc).__name__}: {exc}"))
        raise


async def run_two_processes(user_id: str, modes: tuple[str, str]) -> list[list[str]]:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    import_lock = context.Lock()
    output = context.Queue()
    processes = [
        context.Process(
            target=_process_wallet_requests,
            args=(
                user_id,
                f"{user_id}-{index}" if mode == "different" else user_id,
                mode,
                10,
                barrier,
                output,
                import_lock,
            ),
        )
        for index, mode in enumerate(modes)
    ]
    try:
        for process in processes:
            process.start()
        await asyncio.to_thread(barrier.wait, 40)
        for process in processes:
            await asyncio.to_thread(process.join, 40)
            assert process.exitcode == 0
        packed = [output.get(timeout=2) for _ in processes]
        failed = [item[1] for item in packed if item[0] == "err"]
        if failed:
            raise RuntimeError("wallet worker failed: " + " | ".join(failed))
        return [item[1] for item in packed]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        output.close()
        output.join_thread()


@_aio
async def test_two_processes_twenty_requests_one_operation() -> None:
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation

    key = "concurrent-same-" + uuid4().hex
    result = await run_two_processes(key, ("same", "same"))
    assert len(result) == 2 and all(len(items) == 10 for items in result)
    assert len({digest for items in result for digest in items}) == 1
    operations = await RHWalletOperation.get_job_operations(key)
    assert len(operations) == 1
    assert operations[0].billed_delta_points == 100
    assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 7900


@_aio
async def test_different_keys_concurrently_deduct_without_lost_updates() -> None:
    from RH_ComfyUI.utils.database.models import RHBind

    key = "concurrent-different-" + uuid4().hex
    result = await run_two_processes(key, ("different", "different"))
    assert len({digest for items in result for digest in items}) == 20
    assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 7800


@pytest.mark.parametrize("changed_mode", ["changed_amount", "changed_actor", "changed_hash"])
@_aio
async def test_concurrent_changed_commands_conflict_without_another_charge(changed_mode: str) -> None:
    from RH_ComfyUI.utils.database.models import RHWalletOperation

    key = "conflicting-race-" + uuid4().hex
    results = await run_two_processes(key, ("same", changed_mode))
    flat = [item for group in results for item in group]
    assert flat.count("conflict") == 10
    receipts = await RHWalletOperation.get_job_operations(key)
    assert len(receipts) == 1 and receipts[0].billed_delta_points in (100, 101)


@_aio
async def test_concurrent_insufficient_balance_commits_only_affordable_operations() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation
    from RH_ComfyUI.core.billing.tier_quota import now_ts, start_of_local_day, start_of_local_week

    key = "insufficient-race-" + uuid4().hex
    now = now_ts()
    async with base_models.async_maker() as session:
        session.add(
            RHBind(
                user_id=key,
                bot_id="fixture",
                point=25,
                point_5h=25,
                point_day=25,
                point_week=25,
                refreshed_at_5h=now,
                refreshed_at_day=start_of_local_day(now),
                refreshed_at_week=start_of_local_week(now),
            )
        )
        await session.commit()
    await run_two_processes(key, ("different", "different"))
    async with base_models.async_maker() as session:
        operations = list(
            (await session.execute(select(RHWalletOperation).where(col(RHWalletOperation.user_id) == key)))
            .scalars()
            .all()
        )
    assert len(operations) == 20
    assert sum(op.status == "committed" for op in operations) == 2
    assert sum(op.status == "declined" for op in operations) == 18
    assert sum(op.billed_delta_points for op in operations) == 20
    assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 5
    await RHBind.force_refill(key, "fixture")
    replayed = await run_two_processes(key, ("different", "different"))
    assert {item for group in replayed for item in group} == {op.receipt_digest for op in operations}
    assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 8000


@_aio
async def test_settle_and_refund_compete_without_over_refund() -> None:
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation
    from RH_ComfyUI.core.billing.points_api import charge_points_once

    key = "concurrent-close-" + uuid4().hex
    await charge_points_once(operation_command(key))
    await run_two_processes(key, ("settle", "refund"))
    operations = await RHWalletOperation.get_job_operations(key)
    assert len(operations) == 3
    assert sum(op.billed_delta_points for op in operations) == 0
    assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 8000


@pytest.mark.parametrize("legacy_mode", ["quota", "create", "tier", "refill", "add", "deduct"])
@_aio
async def test_legacy_wallet_paths_cannot_overwrite_new_charges(legacy_mode: str) -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind
    from RH_ComfyUI.core.billing.tier_quota import now_ts, start_of_local_day, start_of_local_week

    key = "legacy-race-" + uuid4().hex
    now = now_ts()
    async with base_models.async_maker() as session:
        session.add(
            RHBind(
                user_id=key,
                bot_id="fixture",
                point=5000,
                point_5h=5000,
                point_day=6000,
                point_week=7000,
                refreshed_at_5h=now,
                refreshed_at_day=start_of_local_day(now),
                refreshed_at_week=start_of_local_week(now),
            )
        )
        await session.commit()
    await run_two_processes(key, ("different", legacy_mode))
    row = await RHBind.select_data(user_id=key, bot_id="fixture")
    assert row is not None
    legacy_delta = 100 if legacy_mode == "add" else (-100 if legacy_mode == "deduct" else 0)
    assert row.point_day == 5900 + legacy_delta
    assert row.point_week == 6900 + legacy_delta


@_aio
async def test_expired_legacy_query_and_new_charge_share_one_refresh() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind

    key = "expired-race-" + uuid4().hex
    async with base_models.async_maker() as session:
        session.add(
            RHBind(
                user_id=key,
                bot_id="fixture",
                point=500,
                point_5h=500,
                point_day=500,
                point_week=500,
                refreshed_at_5h=0,
                refreshed_at_day=1,
                refreshed_at_week=1,
            )
        )
        await session.commit()
    await run_two_processes(key, ("different", "quota"))
    row = await RHBind.select_data(user_id=key, bot_id="fixture")
    assert row is not None
    assert (row.point_5h, row.point_day, row.point_week) == (400, 19900, 79900)


def _process_commit_before_ack(key: str, committed: Event, hold_ack: Event) -> None:
    try:
        from pytest_socket import disable_socket

        disable_socket(allow_unix_socket=True)
    except ImportError:
        pass

    async def commit() -> None:
        from gsuid_core.utils.database import base_models
        from RH_ComfyUI.core.billing.points_api import charge_points_once

        await attach_wallet_sandbox(wallet_sandbox_path())
        await charge_points_once(operation_command(key))
        await base_models.engine.dispose()

    asyncio.run(commit())
    committed.set()
    hold_ack.wait(60)


@_aio
async def test_killed_after_commit_is_recovered_by_fresh_processes() -> None:
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation

    key = "lost-ack-" + uuid4().hex
    context = multiprocessing.get_context("spawn")
    committed, hold_ack = context.Event(), context.Event()
    process = context.Process(target=_process_commit_before_ack, args=(key, committed, hold_ack))
    try:
        process.start()
        assert await asyncio.to_thread(committed.wait, 40)
        receipt = await RHWalletOperation.get_operation(f"{key}:charge:v1")
        assert receipt is not None
        process.terminate()
        await asyncio.to_thread(process.join, 5)
        assert process.exitcode is not None and process.exitcode != 0
        recovered = await run_two_processes(key, ("same", "same"))
        assert {digest for items in recovered for digest in items} == {receipt.receipt_digest}
        assert len(await RHWalletOperation.get_job_operations(key)) == 1
        assert (await RHBind.get_quota_status(key, "fixture"))["available"] == 7900
    finally:
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 5)


@_aio
async def test_failed_refund_never_returns_a_normal_balance() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind
    from RH_ComfyUI.core.billing.points_api import refund_points

    key = "failed-refund-" + uuid4().hex
    await RHBind.create_data(key, "fixture")
    trigger = "refund_fault_" + uuid4().hex
    async with base_models.engine.begin() as connection:
        await connection.execute(
            text(
                f"CREATE TRIGGER {trigger} BEFORE UPDATE ON rhbind WHEN NEW.user_id = '{key}' "
                "BEGIN SELECT RAISE(ABORT, 'REFUND_WRITE_FAILED'); END"
            )
        )
    try:
        with pytest.raises(IntegrityError, match="REFUND_WRITE_FAILED"):
            await refund_points(key, "fixture", 10)
    finally:
        async with base_models.engine.begin() as connection:
            await connection.execute(text(f"DROP TRIGGER {trigger}"))


@pytest.mark.parametrize("tokens", [488025, 4880250])
@pytest.mark.parametrize("pre_recorded", [False, True])
@_aio
async def test_maintenance_uses_the_same_settlement_receipt(tokens: int, pre_recorded: bool) -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHWalletOperation, RHComfyuiTaskRecord
    from RH_ComfyUI.core.billing.reconcile import reconcile_seedance_usage_billing, actual_points_for_seedance_record
    from RH_ComfyUI.core.billing.points_api import charge_points_once

    key = "maintenance-" + uuid4().hex
    await charge_points_once(operation_command(key))
    raw = json.dumps({"usage": {"completion_tokens": tokens}, "resolution": "1080p", "duration": 5})
    actual = actual_points_for_seedance_record(
        task_name="seedance2.5",
        raw_response=raw,
        resolution="1080p",
        duration_seconds=5,
    )
    assert actual is not None and actual > 100
    async with base_models.async_maker() as session:
        record = RHComfyuiTaskRecord(
            user_id=key,
            bot_id="fixture",
            task_name="seedance2.5",
            task_type="video",
            entry_point="http",
            status="ok",
            point_cost=actual if pre_recorded else 100,
            trace_id=key,
            resolution="1080p",
            duration_seconds=5,
            raw_response_json=raw,
        )
        session.add(record)
        await session.commit()
        record_id = record.id
    await reconcile_seedance_usage_billing(apply=True, adjust_wallet=True)
    receipt = await RHWalletOperation.get_operation(f"{key}:settle:v1")
    assert receipt is not None
    assert receipt.net_after_points == (actual if actual <= 8000 else 100)
    assert receipt.status == ("committed" if actual <= 8000 else "declined")
    async with base_models.async_maker() as session:
        stored = await session.get(RHComfyuiTaskRecord, record_id)
        assert stored is not None
        assert stored.point_cost == receipt.net_after_points
    await reconcile_seedance_usage_billing(apply=True, adjust_wallet=True)
    assert len(await RHWalletOperation.get_job_operations(key)) == 2


@pytest.mark.parametrize("wrong_field", ["user", "bot"])
@_aio
async def test_managed_reference_identity_conflict_never_falls_back_to_legacy(wrong_field: str) -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind, RHWalletOperation, RHComfyuiTaskRecord
    from RH_ComfyUI.core.billing.reconcile import reconcile_seedance_usage_billing
    from RH_ComfyUI.core.billing.points_api import charge_points_once

    key = "identity-" + uuid4().hex
    await charge_points_once(operation_command(key))
    wrong_user = key + "-other" if wrong_field == "user" else key
    wrong_bot = "other-pool" if wrong_field == "bot" else "fixture"
    await RHBind.create_data(wrong_user, wrong_bot)
    raw = json.dumps({"usage": {"completion_tokens": 488025}, "resolution": "1080p", "duration": 5})
    async with base_models.async_maker() as session:
        record = RHComfyuiTaskRecord(
            user_id=wrong_user,
            bot_id=wrong_bot,
            task_name="seedance2.5",
            task_type="video",
            entry_point="http",
            status="ok",
            point_cost=100,
            trace_id=key,
            resolution="1080p",
            duration_seconds=5,
            raw_response_json=raw,
        )
        session.add(record)
        await session.commit()
        record_id = record.id
    result = await reconcile_seedance_usage_billing(apply=True, adjust_wallet=True)
    assert result["errors"] >= 1
    assert (await RHBind.get_quota_status(wrong_user, wrong_bot))["available"] == 8000
    assert len(await RHWalletOperation.get_job_operations(key)) == 1
    async with base_models.async_maker() as session:
        stored = await session.get(RHComfyuiTaskRecord, record_id)
        assert stored is not None and stored.point_cost == 100


@_aio
async def test_readonly_maintenance_reports_current_net_without_wallet_or_stat_writes() -> None:
    from gsuid_core.utils.database import base_models
    from RH_ComfyUI.utils.database.models import RHBind, RHComfyuiTaskRecord
    from RH_ComfyUI.core.billing.reconcile import reconcile_seedance_usage_billing
    from RH_ComfyUI.core.billing.points_api import charge_points_once, refund_points_once, settle_points_once

    key = "readonly-net-" + uuid4().hex
    await charge_points_once(operation_command(key))
    await settle_points_once(operation_command(key, "settle", 150))
    raw = json.dumps({"usage": {"completion_tokens": 488025}, "resolution": "1080p", "duration": 5})
    async with base_models.async_maker() as session:
        row = RHComfyuiTaskRecord(
            user_id=key,
            bot_id="fixture",
            task_type="video",
            task_name="seedance2.5",
            entry_point="http",
            status="ok",
            point_cost=90,
            trace_id=key,
            raw_response_json=raw,
            resolution="1080p",
            duration_seconds=5,
        )
        session.add(row)
        await session.commit()
        record_id = row.id
    for expected in (150, 0):
        if expected == 0:
            await refund_points_once(operation_command(key, "refund", 0))
        before = (await RHBind.get_quota_status(key, "fixture"))["available"]
        result = await reconcile_seedance_usage_billing(apply=True, adjust_wallet=False)
        item = next(change for change in result["changes"] if change["record_id"] == record_id)
        assert item["actual"] == expected and item["delta"] == 0
        assert (await RHBind.get_quota_status(key, "fixture"))["available"] == before
        async with base_models.async_maker() as session:
            stored = await session.get(RHComfyuiTaskRecord, record_id)
            assert stored is not None and stored.point_cost == 90


@_aio
async def test_upgrade_from_legacy_wallet_preserves_rows_and_adds_receipt_constraints(tmp_path: Path) -> None:
    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from RH_ComfyUI.utils.database.models import WALLET_OPERATION_MIGRATIONS, RHBind, RHWalletOperation
    from RH_ComfyUI.core.billing.tier_quota import now_ts, start_of_local_day, start_of_local_week

    database_path = _refuse_live_sqlite(tmp_path / ("wallet-upgrade-" + uuid4().hex + ".sqlite"))
    assert not database_path.exists()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    key = "upgrade-" + uuid4().hex
    now = now_ts()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(RHBind.__table__.create)
            await connection.execute(
                insert(RHBind).values(
                    user_id=key,
                    bot_id="fixture",
                    vip_tier="free",
                    point=17,
                    point_5h=17,
                    point_day=99,
                    point_week=777,
                    refreshed_at_5h=now,
                    refreshed_at_day=start_of_local_day(now),
                    refreshed_at_week=start_of_local_week(now),
                )
            )
        for _ in range(2):
            async with engine.begin() as connection:
                await connection.run_sync(RHWalletOperation.metadata.create_all)
                for statement in WALLET_OPERATION_MIGRATIONS:
                    await connection.execute(text(statement))
        async with sessions() as session:
            row = (await session.execute(select(RHBind).where(col(RHBind.user_id) == key))).scalar_one()
            assert (row.point_5h, row.point_day, row.point_week) == (17, 99, 777)
        async with sessions() as session:
            await RHBind.begin_write(session)
            operation = await RHWalletOperation.apply_in_session(session, operation_command(key, points=10))
            await session.commit()
        for overrides in (
            {"operation_key": operation.operation_key + "-other"},
            {"job_key": operation.job_key + "-other"},
        ):
            async with sessions() as session:
                with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
                    await session.execute(
                        insert(RHWalletOperation).values(
                            {
                                **operation.receipt_body(),
                                "receipt_digest": operation.receipt_digest,
                                **overrides,
                            }
                        )
                    )
                await session.rollback()
        async with sessions() as session:
            with pytest.raises(IntegrityError, match="WALLET_RECEIPT_IMMUTABLE"):
                await session.execute(text("UPDATE rhwalletoperation SET reason='tampered'"))
            await session.rollback()
    finally:
        await engine.dispose()
