"""Opt-in bounded storage measurements; these are not production capacity claims."""

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from uuid import uuid4

import asyncpg
import pytest

from ledger.formula_engine import Formula, evaluate_graph
from ledger.store import LedgerStore
from ledger.typed_storage import lock_workbook, read_snapshot
from tests.integration.postgres import POSTGRES_TEST_URL

pytestmark = pytest.mark.skipif(
    os.environ.get("LEDGER_STORAGE_PROFILE") != "1",
    reason="Set LEDGER_STORAGE_PROFILE=1 for bounded storage measurements",
)


async def test_bounded_storage_profile():
    """Measure whole-grid reads, full-graph evaluation, locks and pool occupancy."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    workbooks = []
    report = {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "platform": platform.platform(),
        "limits": "Local single-client sample; 10 repetitions; graph evaluation excludes persistence; contention hold/timeout is deliberately 50 ms",
        "samples": [],
        "status": "running",
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path("tests/e2e/outputs") / f"storage-profile-{stamp}-{uuid4().hex[:8]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        for row_count, managed in ((32, 32), (512, 128), (4096, 256)):
            workspace = (
                await store.create_spreadsheet(90231, f"storage-profile-{uuid4().hex}")
            )["spreadsheetId"]
            workbooks.append(workspace)
            await store.create_sheet(
                workspace,
                "Source",
                [
                    ["ID", "Amount", "Description"],
                    *[[str(row), row, "x" * 96] for row in range(row_count)],
                ],
            )
            formulas = {
                f"cell-{index}": Formula.model_validate(
                    {
                        "operation": "add",
                        "operands": [
                            {"cell_id": f"cell-{index - 1}"},
                            {"literal": "1"},
                        ],
                        "rounding": {"places": 2, "mode": "ROUND_HALF_EVEN"},
                    }
                )
                for index in range(1, managed)
            }
            sample = {
                "source_rows": row_count,
                "managed_cells": managed,
                "formula_count": len(formulas),
                "read_seconds": [],
                "graph_seconds": [],
            }
            for _ in range(10):
                async with store.pool.acquire() as connection:
                    started = time.perf_counter()
                    snapshot = await read_snapshot(connection, 90231, workspace)
                    sample["read_seconds"].append(time.perf_counter() - started)
                sample["source_bytes"] = sum(
                    len(sheet["grid"].encode()) for sheet in snapshot["sheets"]
                )
                started = time.perf_counter()
                calculated = evaluate_graph({"cell-0": "1"}, formulas)
                sample["graph_seconds"].append(time.perf_counter() - started)
                assert len(calculated) == managed - 1
                assert calculated[f"cell-{managed - 1}"]["value"] == f"{managed}.00"
            report["samples"].append(sample)

        async with store.pool.acquire() as holder, holder.transaction():
            await lock_workbook(holder, 90231, workbooks[-1])
            async with store.pool.acquire() as contender:
                async with contender.transaction():
                    started = time.perf_counter()
                    await lock_workbook(contender, 90231, workbooks[0])
                    report["other_workbook_lock_seconds"] = (
                        time.perf_counter() - started
                    )
                async with contender.transaction():
                    await contender.execute("SET LOCAL lock_timeout = '50ms'")
                    started = time.perf_counter()
                    with pytest.raises(asyncpg.LockNotAvailableError):
                        await lock_workbook(contender, 90231, workbooks[-1])
                    report["same_workbook_lock_timeout_seconds"] = (
                        time.perf_counter() - started
                    )
        async with store.pool.acquire() as connection, connection.transaction():
            started = time.perf_counter()
            await lock_workbook(connection, 90231, workbooks[-1])
            report["lock_after_release_seconds"] = time.perf_counter() - started

        async with AsyncExitStack() as stack:
            for _ in range(5):
                await stack.enter_async_context(store.pool.acquire())
            report["occupied_connections"] = (
                store.pool.get_size() - store.pool.get_idle_size()
            )
            started = time.perf_counter()
            with pytest.raises(asyncio.TimeoutError):
                await store.pool.acquire(timeout=0.05)
            report["saturated_acquire_timeout_seconds"] = time.perf_counter() - started
        assert store.pool.get_idle_size() == store.pool.get_size()
        report["idle_connections_after_release"] = store.pool.get_idle_size()
        report["status"] = "passed"
    finally:
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        for workspace in reversed(workbooks):
            await store.delete_spreadsheet(workspace)
        await store.close()
        print(f"Storage profile: {path}")
