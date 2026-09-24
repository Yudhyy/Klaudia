"""Policy-backed reconciliation against real owned PostgreSQL tables."""

import json
from uuid import uuid4

import pytest

from app.services.catalogue.service import CatalogueService
from app.services.memory.contracts import DocumentEdit, DocumentPath
from app.services.memory.reconciliation import PolicyReconciliationTools
from app.services.memory.store import MemoryDocumentStore
from ledger.catalogue import CatalogueStore
from ledger.errors import RevisionConflictError
from ledger.resources import ResourceNotFoundError, TableRegistration
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL
from tests.unit.test_accounting_policy import policy_fields
from tests.unit.test_policy_reconciliation import arguments


@pytest.fixture
async def policy_setup(postgres_db):
    """Create exact source tables and an explicit policy for the isolated owner."""
    ledger = LedgerStore(POSTGRES_TEST_URL)
    await ledger.connect()
    catalogue = CatalogueStore(ledger.pool)
    documents = MemoryDocumentStore(postgres_db.pool)
    await documents.initialize()
    await documents.write(
        1,
        DocumentPath.ACCOUNTING_POLICY,
        DocumentEdit(
            expected_revision=0,
            content="Approved reconciliation rules",
            policy=policy_fields(),
        ),
    )
    workbooks, tables = [], []
    try:
        for amount in ("1.123456789012345678901", "1.1"):
            workbook = await ledger.create_spreadsheet(1, f"policy-{uuid4().hex}")
            workspace = workbook["spreadsheetId"]
            workbooks.append(workspace)
            sheet = await ledger.create_sheet(
                workspace,
                "Amounts",
                [["ID", "Amount", "Currency"], ["a", amount, "USD"]],
            )
            tables.append(
                await catalogue.register(
                    workspace,
                    TableRegistration(
                        sheet_id=sheet["sheetId"],
                        expected_sheet_revision=0,
                        table_range="A1:C2",
                        name="Amounts",
                        grain="one invoice",
                        entity="Example Ltd",
                    ),
                )
            )
        service = CatalogueService(catalogue)
        yield ledger, documents, service, workbooks, tables
    finally:
        for workspace in workbooks:
            await ledger.delete_spreadsheet(workspace)
        await ledger.close()


def request_for(tables):
    """Select the fixture table identities without supplying numeric policy."""
    return arguments(
        table_id=tables[0]["table_id"], right_table_id=tables[1]["table_id"]
    )


async def test_policy_reconciliation_exact_evidence_and_no_writes(policy_setup):
    """Report exact difference and all checked policy fields without changing grids."""
    ledger, documents, catalogue, _, tables = policy_setup
    before = await ledger.pool.fetch(
        "SELECT sheet_id, grid::text FROM ledger_sheet WHERE sheet_id = ANY($1::int[])",
        [table["sheet_id"] for table in tables],
    )
    tool = PolicyReconciliationTools(documents, catalogue, 1).tool
    evidence = await tool.ainvoke(request_for(tables))
    assert evidence["records"][0]["difference"] == "0.023456789012345678901"
    assert evidence["records"][0]["status"] == "different"
    assert evidence["query"]["query"]["tolerance"] == "0.01"
    assert evidence["policy_evidence"]["parameters"] == policy_fields()
    after = await ledger.pool.fetch(
        "SELECT sheet_id, grid::text FROM ledger_sheet WHERE sheet_id = ANY($1::int[])",
        [table["sheet_id"] for table in tables],
    )
    assert before == after


@pytest.mark.parametrize("column,value", [("entity", None), ("entity", "Other Ltd")])
async def test_policy_rejects_unmatched_table_entity(policy_setup, column, value):
    """Block both absent and mismatched registered entity evidence."""
    ledger, documents, catalogue, _, tables = policy_setup
    await ledger.pool.execute(
        "UPDATE ledger_resource SET entity = $1, revision = revision + 1 WHERE resource_id = $2",
        value,
        tables[1]["table_id"],
    )
    with pytest.raises(ValueError, match="entity"):
        await PolicyReconciliationTools(documents, catalogue, 1).tool.ainvoke(
            request_for(tables)
        )


async def test_policy_requires_actual_source_units(policy_setup):
    """Reject foreign currency in a checked unit column instead of trusting intent."""
    ledger, documents, catalogue, _, tables = policy_setup
    await ledger.pool.execute(
        "UPDATE ledger_sheet SET grid = jsonb_set(grid, '{1,2}', '\"EUR\"') WHERE sheet_id = $1",
        tables[1]["sheet_id"],
    )
    await ledger.pool.execute(
        "UPDATE ledger_resource SET source_revision = 1 WHERE resource_id = $1",
        tables[1]["table_id"],
    )
    with pytest.raises(ValueError, match="unit"):
        await PolicyReconciliationTools(documents, catalogue, 1).tool.ainvoke(
            request_for(tables)
        )


async def test_policy_scope_rechecks_current_source_ownership(policy_setup):
    """Reject either foreign source without returning partial comparison evidence."""
    ledger, documents, catalogue, workbooks, tables = policy_setup
    await ledger.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = 999 WHERE spreadsheet_id = $1",
        workbooks[1],
    )
    with pytest.raises(ResourceNotFoundError):
        await PolicyReconciliationTools(documents, catalogue, 1).tool.ainvoke(
            request_for(tables)
        )


async def test_changed_policy_during_calculation_discards_results(
    policy_setup, monkeypatch
):
    """A real concurrent deletion invalidates the policy-bound read result."""
    _, documents, catalogue, _, tables = policy_setup
    execute = catalogue.financial_query

    async def delete_during_read(owner, query):
        """Delete saved policy after the exact financial snapshot returns."""
        evidence = await execute(owner, query)
        await documents.delete(1, DocumentPath.ACCOUNTING_POLICY, 1)
        return evidence

    monkeypatch.setattr(catalogue, "financial_query", delete_during_read)
    with pytest.raises(RevisionConflictError, match="Policy changed"):
        await PolicyReconciliationTools(documents, catalogue, 1).tool.ainvoke(
            request_for(tables)
        )


async def test_changed_entity_after_inspection_cannot_mix_revisions(
    policy_setup, monkeypatch
):
    """Bind the checked entity to the exact catalogue revision used by execution."""
    ledger, documents, catalogue, _, tables = policy_setup
    execute = catalogue.financial_query

    async def change_entity_before_read(owner, query):
        """Change metadata between applicability inspection and source snapshot."""
        await ledger.pool.execute(
            "UPDATE ledger_resource SET entity = 'Other', revision = revision + 1 WHERE resource_id = $1",
            tables[1]["table_id"],
        )
        return await execute(owner, query)

    monkeypatch.setattr(catalogue, "financial_query", change_entity_before_read)
    with pytest.raises(RevisionConflictError):
        await PolicyReconciliationTools(documents, catalogue, 1).tool.ainvoke(
            request_for(tables)
        )


async def test_scripted_chat_uses_policy_skill_and_preserves_evidence(
    policy_setup, postgres_db
):
    """Exercise context read, versioned skill and checked comparison through chat."""
    from app.services.core.main_chat import MainChatService
    from langchain_core.messages import AIMessage
    from tests.unit.test_main_agent import ScriptedModel, call
    from tests.unit.test_main_chat import chat_turn
    from dataclasses import replace

    _, documents, catalogue, _, tables = policy_setup
    session = await postgres_db.create_session(1)
    model = ScriptedModel(
        [
            call("load_skill", {"name": "policy-reconciliation"}, "skill"),
            call("read_memory_document", {"path": "/accounting-policy.md"}, "policy"),
            call("reconcile_with_policy", request_for(tables), "comparison"),
            AIMessage(
                content="The difference is 0.023456789012345678901 USD under policy revision 1."
            ),
        ]
    )
    outcome = await MainChatService(
        model, catalogue, postgres_db, memory_documents=documents
    ).run(replace(chat_turn(), user_id=1, session_id=session))
    assert outcome.loaded_skills["policy-reconciliation"] == "3"
    assert outcome.operation_receipts == ()
    evidence = json.loads(outcome.tool_evidence[-1][2])
    assert evidence["policy_evidence"]["revision"] == 1
    assert evidence["records"][0]["difference"] == "0.023456789012345678901"


@pytest.mark.parametrize("change", ["policy", "ownership"])
async def test_task_replay_rechecks_policy_and_source_access(
    policy_setup, postgres_db, change
):
    """Saved reconciliation evidence cannot bypass later policy or ownership changes."""
    from app.services.core.main_chat import MainChatService
    from app.services.workflow.store import TaskStore
    from langchain_core.messages import AIMessage
    from tests.unit.test_main_agent import ScriptedModel, call
    from tests.unit.test_main_chat import chat_turn
    from dataclasses import replace

    ledger, documents, catalogue, workbooks, tables = policy_setup
    tasks = TaskStore(POSTGRES_TEST_URL)
    await tasks.connect()
    try:
        session = await postgres_db.create_session(1)
        model = ScriptedModel(
            [
                call("reconcile_with_policy", request_for(tables)),
                AIMessage(content="Difference recorded."),
            ]
        )
        service = MainChatService(
            model, catalogue, postgres_db, memory_documents=documents, tasks=tasks
        )
        outcome = await service.run(replace(chat_turn(), user_id=1, session_id=session))
        assert outcome.status == "answered"
        assert (
            json.loads(outcome.tool_evidence[-1][2])["policy_evidence"]["revision"] == 1
        )
        _, repeated = await service.resume(1, outcome.task_id)
        assert repeated.tool_evidence == outcome.tool_evidence
        if change == "policy":
            await documents.delete(1, DocumentPath.ACCOUNTING_POLICY, 1)
            error = RevisionConflictError
        else:
            await ledger.pool.execute(
                "UPDATE ledger_spreadsheet SET user_id = 999 WHERE spreadsheet_id = $1",
                workbooks[1],
            )
            error = ResourceNotFoundError
        with pytest.raises(error):
            await service.resume(1, outcome.task_id)
    finally:
        await tasks.close()
