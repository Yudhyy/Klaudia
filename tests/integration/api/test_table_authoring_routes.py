"""Real authenticated authoring and main-agent lifecycle integration."""

import json

import pytest
from langchain_core.messages import AIMessage

from app.routes.v1.table_authoring import router
from app.services.catalogue.authoring import AuthoringService
from ledger.catalogue import CatalogueStore
from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.integration.api.test_durable_tasks import durable_client  # noqa: F401
from tests.unit.test_main_agent import ScriptedModel, call


@pytest.fixture
async def authoring_client(durable_client):  # noqa: F811
    """Enable the same authoring service and router as production main chat."""
    fixture = durable_client
    fixture.container.authoring = AuthoringService(fixture.fixture.store.pool)
    fixture.container.main_chat._authoring = fixture.container.authoring
    fixture.client._transport.app.include_router(router, prefix="/v1")
    return fixture


async def test_authoring_api_checks_scope_and_commits_once(authoring_client):
    """An authenticated client can create a second table without affecting the first."""
    fixture = authoring_client
    sheets = await fixture.client.get("/v1/catalogue/sheets", headers=fixture.headers)
    assert sheets.status_code == 200, sheets.text
    sheet_id = next(
        item["sheet_id"]
        for item in sheets.json()["sheets"]
        if item["spreadsheet_id"] == fixture.fixture.workbook_id
    )
    endpoint = f"/v1/catalogue/sheets/{sheet_id}/region"
    assert (
        await fixture.client.get(endpoint, params={"table_range": "H1:I3"})
    ).status_code == 401
    inspected = await fixture.client.get(
        endpoint, params={"table_range": "H1:I3"}, headers=fixture.headers
    )
    assert inspected.status_code == 200, inspected.text
    body = {
        "action": "create_table",
        "headers": ["Category", "Budget"],
        "definition": {
            "sheet_id": sheet_id,
            "expected_sheet_revision": inspected.json()["sheet_revision"],
            "name": "Budgets",
            "table_range": "H1:I3",
        },
    }
    prepared = await fixture.client.post(
        "/v1/catalogue/proposals", json=body, headers=fixture.headers
    )
    assert prepared.status_code == 200, prepared.text
    reference = prepared.json()["operation_ref"]
    committed = await fixture.client.post(
        f"/v1/catalogue/operations/{reference}/execute", headers=fixture.headers
    )
    assert committed.status_code == 200, committed.text
    repeated = await fixture.client.post(
        f"/v1/catalogue/operations/{reference}/execute", headers=fixture.headers
    )
    assert repeated.json() == committed.json()
    assert committed.json()["changes"]["cells_changed"] == 2
    descriptor = await CatalogueStore(fixture.fixture.store.pool).inspect_owned(
        fixture.user_id, committed.json()["target"]["table_id"]
    )
    assert [column["name"] for column in descriptor["columns"]] == [
        "Category",
        "Budget",
    ]
    foreign = await fixture.client.post(
        "/v1/catalogue/proposals",
        json={**body, "user_id": 99999},
        headers=fixture.headers,
    )
    assert foreign.status_code == 422
    missing = await fixture.client.get(
        "/v1/catalogue/sheets/2147483647/region",
        params={"table_range": "A1:B2"},
        headers=fixture.headers,
    )
    assert missing.status_code == 404


async def test_main_agent_authors_table_and_keyed_replay_retains_receipt(
    authoring_client,
):
    """The durable main agent discovers placement, creates a table and replays once."""
    fixture = authoring_client

    class CreateModel(ScriptedModel):
        """Choose a blank region using real authoring tool evidence."""

        async def ainvoke(self, messages, config=None):
            """Advance only from the preceding tool's observed IDs and revisions."""
            previous = messages[-1]
            if previous.type != "tool":
                return call(
                    "list_authoring_sheets",
                    {"workbook_id": fixture.fixture.workbook_id},
                )
            evidence = json.loads(previous.content)
            if previous.name == "list_authoring_sheets":
                return call(
                    "inspect_sheet_region",
                    {
                        "sheet_id": evidence["sheets"][0]["sheet_id"],
                        "table_range": "H1:I3",
                    },
                    "region",
                )
            if previous.name == "inspect_sheet_region":
                return call(
                    "prepare_table_authoring",
                    {
                        "action": "create_table",
                        "headers": ["Category", "Budget"],
                        "definition": {
                            "sheet_id": evidence["sheet_id"],
                            "expected_sheet_revision": evidence["sheet_revision"],
                            "table_range": evidence["range"],
                            "name": "Budgets",
                        },
                    },
                    "prepare",
                )
            if previous.name == "prepare_table_authoring":
                return call(
                    "execute_operation",
                    {"operation_ref": evidence["operation_ref"]},
                    "execute",
                )
            return AIMessage(content="Created the budget table.")

    fixture.container.main_chat._model = CreateModel([])
    body = {
        **fixture.request,
        "messages": [
            {
                "role": "user",
                "content": "Create a budget table with Category and Budget columns.",
            }
        ],
    }
    committed = await fixture.client.post(
        "/v1/chat", json=body, headers=fixture.headers
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["run_status"] == "answered"
    assert committed.json()["operation_receipts"][0]["operation_type"] == "create_table"
    fixture.container.main_chat._model = ScriptedModel([])
    repeated = await fixture.client.post("/v1/chat", json=body, headers=fixture.headers)
    assert (
        repeated.json()["operation_receipts"] == committed.json()["operation_receipts"]
    )
    assert repeated.json()["task_id"] == committed.json()["task_id"]


async def test_main_unregister_approval_executes_then_resumes(authoring_client):
    """Human approval removes only catalogue identity and resume returns its receipt."""
    from unittest.mock import AsyncMock

    from app.routes.v1.approvals import router as approvals_router
    from app.services.core.approvals import ApprovalService
    from app.services.workflow.approvals import CheckedApprovals

    fixture = authoring_client
    approvals = ApprovalService(fixture.container.db_client, AsyncMock())
    await approvals.ensure_schema()
    approvals.checked = CheckedApprovals(fixture.fixture.store.pool)
    fixture.container.approvals = approvals
    fixture.client._transport.app.include_router(approvals_router, prefix="/v1")
    table = await fixture.fixture.store.pool.fetchrow(
        "SELECT r.resource_id, r.revision, s.revision AS sheet_revision FROM ledger_resource r JOIN ledger_sheet s ON s.sheet_id=r.sheet_id WHERE s.workspace=$1",
        fixture.fixture.workbook_id,
    )

    class UnregisterModel(ScriptedModel):
        """Execute only the reference returned by unregister preparation."""

        async def ainvoke(self, messages, config=None):
            """Use the stored proposal, then acknowledge the committed receipt."""
            previous = messages[-1]
            if previous.type == "tool" and previous.name == "prepare_table_authoring":
                return call(
                    "execute_operation",
                    {"operation_ref": json.loads(previous.content)["operation_ref"]},
                    "execute",
                )
            if previous.type == "tool" and previous.name == "execute_operation":
                return AIMessage(
                    content="Unregistered the table. All cells remain unchanged."
                )
            return await super().ainvoke(messages, config)

    fixture.container.main_chat._model = UnregisterModel(
        [
            call(
                "prepare_table_authoring",
                {
                    "action": "unregister_table",
                    "table_id": table["resource_id"],
                    "expected_sheet_revision": table["sheet_revision"],
                    "expected_catalogue_revision": table["revision"],
                },
            )
        ]
    )
    before = await fixture.fixture.observe_state()
    body = {
        **fixture.request,
        "messages": [
            {
                "role": "user",
                "content": "Unregister the claims table while keeping its cells.",
            }
        ],
    }
    paused = await fixture.client.post("/v1/chat", json=body, headers=fixture.headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["run_status"] == "awaiting_approval"
    assert "append" not in paused.json()["message"]["content"]
    approval_id = paused.json()["pending_approvals"][0]["approval_id"]
    decision = await fixture.client.post(
        f"/v1/approvals/{approval_id}",
        json={"decision": "approve"},
        headers=fixture.headers,
    )
    assert decision.status_code == 200, decision.text
    resumed = await fixture.client.post(
        f"/v1/tasks/{paused.json()['task_id']}/resume", headers=fixture.headers
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run_status"] == "answered"
    assert resumed.json()["operation_receipts"] == [decision.json()["result"]]
    assert await fixture.fixture.observe_state() == before
