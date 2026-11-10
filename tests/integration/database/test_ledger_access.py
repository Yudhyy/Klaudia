"""Saved workbook checks retain current ownership and the task-lock connection."""

import json
from uuid import uuid4

import pytest

from app.adapters.persistence.tasks import PostgresTaskStore
from ledger_core.resources import ResourceNotFoundError
from ledger_postgres.catalogue import CatalogueStore
from ledger_postgres.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL


async def test_workbook_replay_uses_the_locked_backend(postgres_db, monkeypatch):
    """Reject revoked saved evidence after proving the check's backend and lock."""
    session_id = await postgres_db.create_session(1)
    ledger = LedgerStore(POSTGRES_TEST_URL)
    tasks = PostgresTaskStore(POSTGRES_TEST_URL)
    await ledger.connect_existing()
    await tasks.connect()
    count = CatalogueStore.count_owned_workbooks
    observed = []

    async def observed_count(reader, user_id, workbook_ids):
        """Observe the connection used by the real workbook ownership check.

        Args:
            reader: Task-bound ledger adapter.
            user_id: Original task owner.
            workbook_ids: Identities read from the saved checkpoint.

        Returns:
            Number of workbooks still owned by the caller.
        """
        if workbook_ids:
            async with reader._pool.acquire() as connection:
                backend = await connection.fetchval("SELECT pg_backend_pid()")
                held = await connection.fetchval(
                    "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory' AND granted"
                )
                assert held > 0
                observed.append(backend)
        return await count(reader, user_id, workbook_ids)

    monkeypatch.setattr(CatalogueStore, "count_owned_workbooks", observed_count)
    try:
        workbook = await ledger.create_spreadsheet(1, "Saved replay " + uuid4().hex)
        workbook_id = workbook["spreadsheetId"]
        task_id = await tasks.create(
            (1, session_id),
            {
                "turn": {
                    "text": "Read workbook",
                    "document_ids": [],
                    "active_workbook_id": workbook_id,
                }
            },
        )
        async with tasks.open(1, task_id) as task:
            await task.save(
                {
                    "evidence": [
                        [
                            "inspect_typed_workbook",
                            {},
                            json.dumps({"workbook_id": workbook_id}),
                        ]
                    ]
                }
            )
        async with tasks.open(1, task_id) as task:
            backend = await task.connection.fetchval("SELECT pg_backend_pid()")
            assert observed == [backend]
            assert not await postgres_db.fetchval(
                "SELECT pg_try_advisory_lock(hashtextextended($1,0))", (task_id,)
            )
        await postgres_db.execute(
            "UPDATE ledger_spreadsheet SET user_id=$1 WHERE spreadsheet_id=$2",
            (99, workbook_id),
        )
        with pytest.raises(ResourceNotFoundError, match="Saved task source"):
            async with tasks.open(1, task_id):
                pytest.fail("Revoked workbook reached task execution")
    finally:
        await tasks.close()
        await ledger.close()
