"""Load fixed task and ledger records and recover through the real task store."""

from app.adapters.agent.operations import OperationAdapter

from app.adapters.agent.catalogue import CatalogueAdapter

import json

import pytest
from langchain_core.messages import AIMessage

from app.services.catalogue.service import CatalogueService
from app.services.workflow.approvals import CheckedApprovals
from app.services.workflow.store import TaskStore
from klaudia.core.agent.agent import MainAgent
from klaudia.core.agent.context import TaskContext
from ledger.catalogue import CatalogueStore
from ledger.store import LedgerStore
from tests.agent_compatibility import read_fixture
from tests.integration.postgres import POSTGRES_TEST_URL
from tests.unit.test_main_agent import ScriptedModel


@pytest.fixture(params=["pending_approval", "committed_before_checkpoint"])
async def release_task(postgres_db, request):
    """Insert original release rows without preparing a new operation or checkpoint.

    Yields:
        Saved contracts, connected stores and the selected persistence state.
    """
    saved = read_fixture("continuation.json")
    store = LedgerStore(POSTGRES_TEST_URL)
    tasks = TaskStore(POSTGRES_TEST_URL, require_approval=True)
    await store.connect()
    await tasks.connect()
    owner = saved["context"]["user_id"]
    workbook_id = saved["context"]["active_workbook_id"]
    inserted_owner = False
    inserted_workbook = False
    try:
        await postgres_db.execute(
            'INSERT INTO "user" (user_id, username, email, password_hash) '
            "VALUES ($1, 'compatibility', 'compatibility@example.invalid', 'fixture-only')",
            (owner,),
        )
        inserted_owner = True
        await postgres_db.execute(
            "INSERT INTO session (session_id, user_id) VALUES ($1, $2)",
            (saved["session"]["session_id"], owner),
        )
        snapshot = saved["snapshots"][request.param]
        for table, rows in snapshot.items():
            for row in rows:
                columns = ", ".join(f'"{name}"' for name in row)
                await store.pool.execute(
                    f"INSERT INTO {table} ({columns}) "
                    f"SELECT {columns} FROM jsonb_populate_record(NULL::{table}, $1::jsonb)",
                    json.dumps(row),
                )
            if table == "ledger_spreadsheet":
                inserted_workbook = True
        yield saved, store, tasks, request.param
    finally:
        await tasks.close()
        if inserted_workbook:
            await store.pool.execute(
                "DELETE FROM ledger_table_operation WHERE workspace=$1", workbook_id
            )
            await store.delete_spreadsheet(workbook_id)
        if inserted_owner:
            await postgres_db.execute("DELETE FROM session WHERE user_id=$1", (owner,))
            await postgres_db.execute('DELETE FROM "user" WHERE user_id=$1', (owner,))
        await store.close()


async def test_release_task_approval_and_receipt_recovery(release_task) -> None:
    """Resume old pending work and recover the exact committed receipt once."""
    saved, store, tasks, persistence_state = release_task
    context = TaskContext.model_validate(saved["context"])
    task_id = saved["task_id"]
    before = await store.pool.fetchrow(
        "SELECT grid, revision FROM ledger_sheet WHERE workspace=$1",
        context.active_workbook_id,
    )
    progress = await tasks.view(context.user_id, task_id)
    assert progress["pending_tools"] == ["execute_operation"]
    assert progress["operation_references"] == [saved["operation_ref"]]
    if persistence_state == "pending_approval":
        assert progress["pending_approvals"] == [saved["approval"]]
        model = ScriptedModel([])
        async with tasks.open(context.user_id, task_id) as task:
            outcome = await MainAgent(
                model,
                CatalogueAdapter(CatalogueService(CatalogueStore(store.pool))),
                operations=OperationAdapter(task),
            ).run(saved["message"], context, checkpoint=task)
        assert outcome.status == "awaiting_approval"
        assert outcome.pending_approvals == (saved["approval"],)
        assert model.inputs == []
        assert (
            await store.pool.fetchrow(
                "SELECT grid, revision FROM ledger_sheet WHERE workspace=$1",
                context.active_workbook_id,
            )
            == before
        )
        decision = await CheckedApprovals(store.pool).approve(
            context.user_id, saved["approval"]["approval_id"]
        )
        assert {key: decision[key] for key in saved["decision"]} == saved["decision"]
        committed_receipt = decision["result"]
        assert (
            committed_receipt | {"operation_id": saved["receipt"]["operation_id"]}
            == saved["receipt"]
        )
    else:
        assert progress["operation_receipts"] == [saved["receipt"]]
        assert progress["pending_approvals"] == []
        committed_receipt = saved["receipt"]

    committed = await store.pool.fetchrow(
        "SELECT grid, revision FROM ledger_sheet WHERE workspace=$1",
        context.active_workbook_id,
    )
    original_sheet = saved["snapshots"]["committed_before_checkpoint"]["ledger_sheet"][
        0
    ]
    assert json.loads(committed["grid"]) == original_sheet["grid"]
    assert committed["revision"] == original_sheet["revision"]
    model = ScriptedModel([AIMessage(content="Original append confirmed.")])
    async with tasks.open(context.user_id, task_id) as task:
        outcome = await MainAgent(
            model,
            CatalogueAdapter(CatalogueService(CatalogueStore(store.pool))),
            operations=OperationAdapter(task),
        ).run(saved["message"], context, checkpoint=task)
    assert outcome.status == "answered"
    assert outcome.operation_references == (saved["operation_ref"],)
    assert outcome.operation_receipts == (committed_receipt,)
    assert model.inputs[0][-1].tool_call_id == "execute-original"
    assert json.loads(model.inputs[0][-1].content) == committed_receipt
    assert (
        await store.pool.fetchrow(
            "SELECT grid, revision FROM ledger_sheet WHERE workspace=$1",
            context.active_workbook_id,
        )
        == committed
    )
    assert (
        await store.pool.fetchval(
            "SELECT count(*) FROM ledger_table_operation WHERE workspace=$1",
            context.active_workbook_id,
        )
        == 1
    )
