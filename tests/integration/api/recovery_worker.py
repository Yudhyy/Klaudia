"""Disposable child process for testing durable task recovery after SIGKILL."""

import asyncio
import json
import sys

from app.models.chat import ChatMetadata
from app.services.catalogue.service import CatalogueService
from app.services.core.main_chat import MainChatService, MainChatTurn
from app.services.core.operations import OperationService
from app.services.extraction.infra.db_client import AppDBClient
from app.services.workflow.store import TaskSession, TaskStore
from config.settings import Settings
from ledger.catalogue import CatalogueStore
from ledger.store import LedgerStore
from tests.integration.api.test_main_chat_routes import AppendChatModel
from tests.integration.postgres import POSTGRES_TEST_URL


async def run_worker() -> None:
    """Pause at a requested write boundary until the parent kills this process."""
    request = json.loads(sys.stdin.readline())
    database = AppDBClient(Settings(_env_file=None, DATABASE_URL=POSTGRES_TEST_URL))
    ledger = LedgerStore(POSTGRES_TEST_URL)
    tasks = TaskStore(POSTGRES_TEST_URL)
    await database.connect()
    await ledger.connect()
    await tasks.connect()
    original_execute = TaskSession.execute

    async def pause(task: TaskSession) -> None:
        """Signal a persisted task boundary without closing its database connection.

        Args:
            task: Task whose write is about to start or has committed.
        """
        print(
            json.dumps(
                {"task_id": task.record["task_id"], "boundary": request["boundary"]}
            ),
            flush=True,
        )
        await asyncio.Event().wait()

    async def execute_at_boundary(task, owner, reference):
        """Pause before execution or immediately after the real transaction commits.

        Args:
            task: Task holding the advisory lock.
            owner: Authenticated fixture owner.
            reference: Original persisted operation reference.

        Returns:
            The committed receipt if the worker is not killed.
        """
        if request["boundary"] == "before_commit":
            await pause(task)
        receipt = await original_execute(task, owner, reference)
        if request["boundary"] == "after_commit":
            assert receipt["status"] == "committed"
            await pause(task)
        return receipt

    TaskSession.execute = execute_at_boundary
    try:
        service = MainChatService(
            AppendChatModel(),
            CatalogueService(CatalogueStore(ledger.pool)),
            database,
            operations=OperationService(ledger),
            tasks=tasks,
        )
        await service.run(
            MainChatTurn(
                user_id=request["user_id"],
                session_id=request["session_id"],
                active_workbook_id=request["workbook_id"],
                text=request["text"],
                extraction_contexts=(),
                metadata=ChatMetadata(
                    date="2026-09-24", time="00:00:00", timezone="UTC"
                ),
                request_key=request["request_key"],
            )
        )
        raise AssertionError("Worker completed without reaching the requested boundary")
    finally:
        await tasks.close()
        await ledger.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
