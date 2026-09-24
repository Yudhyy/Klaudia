"""Fixed rollout fixtures and strict state/label checks without live setup effects."""

from contextlib import asynccontextmanager
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import re
import time

from app.services.memory.contracts import DocumentEdit, DocumentPath
from ledger.catalogue import CatalogueStore
from ledger.resources import TableRegistration
from tests.e2e.rollout_measurement import UsageMeter

SCENARIOS = (
    "multi_intent",
    "streaming",
    "approval_resume",
    "policy",
    "archive_handoff",
    "foreign_source",
)
ACTUALS = [["ID", "Amount", "Currency"], ["a", 120, "USD"], ["b", 80, "USD"]]
BASELINE = [["ID", "Amount", "Currency"], ["a", 100, "USD"], ["b", 50, "USD"]]
APPENDED = ["c-003", 25, "USD"]


@dataclass(frozen=True)
class RolloutFixture:
    """Owned target, distracting hint and foreign target for a single trial."""

    container: object
    owner: int
    workbooks: tuple[str, str, str]
    tables: tuple[str, str]
    file_id: int
    session_id: int

    async def snapshot(self) -> dict:
        """Read every fixture grid, including unexpected new sheets.

        Returns:
            Complete named grids for all three workbooks.
        """
        store = self.container.ledger_store
        rows = await store.pool.fetch(
            "SELECT workspace, title, grid::text FROM ledger_sheet WHERE workspace = ANY($1::text[]) ORDER BY sheet_id",
            list(self.workbooks),
        )
        return {
            workspace: {
                row["title"]: json.loads(row["grid"])
                for row in rows
                if row["workspace"] == workspace
            }
            for workspace in self.workbooks
        }


@asynccontextmanager
async def rollout_fixture(container, owners):
    """Seed only fresh synthetic owners' workbooks, policy and archived extraction.

    Args:
        container: Existing application services in an isolated environment.
        owners: Fresh authenticated and foreign fixture identities.

    Yields:
        Isolated rollout fixture with exact expected records.
    """
    owner, foreign = owners
    store, database = container.ledger_store, container.db_client
    workbooks, tables = [], []
    file_id = None
    try:
        for identity, title in (
            (owner, "Rollout target"),
            (owner, "Rollout distractor"),
            (foreign, "Foreign rollout"),
        ):
            workspace = (await store.create_spreadsheet(identity, title))[
                "spreadsheetId"
            ]
            workbooks.append(workspace)
            for name, values in (("Actuals", ACTUALS), ("Baseline", BASELINE)):
                sheet = await store.create_sheet(workspace, name, values)
                table = await CatalogueStore(store.pool).register(
                    workspace,
                    TableRegistration(
                        sheet_id=sheet["sheetId"],
                        expected_sheet_revision=0,
                        table_range="A1:C3",
                        name=f"{title} {name}",
                        entity="Rollout entity",
                    ),
                )
                if len(workbooks) == 1:
                    tables.append(table["table_id"])
        await container.memory_documents.write(
            owner,
            DocumentPath.ACCOUNTING_POLICY,
            DocumentEdit(
                expected_revision=0,
                content="Synthetic approved reconciliation rules",
                policy={
                    "entity": "Rollout entity",
                    "jurisdiction": "fixture-only",
                    "effective_from": "2026-01-01",
                    "effective_until": "2026-12-31",
                    "unit": "USD",
                    "numeric_text": "reject",
                    "null_amounts": "reject",
                    "null_keys": "reject",
                    "tolerance": "0.01",
                },
            ),
        )
        session = await database.create_session(owner)
        file_id = await database.fetchval(
            "INSERT INTO metadata_file(session_id,user_id,type,total_pages,file_name,status) VALUES($1,$2,'pdf',1,'rollout-invoice.pdf','completed') RETURNING id",
            (session, owner),
        )
        await database.execute(
            "INSERT INTO pages(metadata_file_id,page,agent_extracted,status) VALUES($1,1,$2,'extracted')",
            (
                file_id,
                json.dumps(
                    {
                        "invoice_id": "INV-003",
                        "currency": "USD",
                        "subtotal": "41.25",
                        "tax": "3.75",
                        "total": "45.00",
                    }
                ),
            ),
        )
        yield RolloutFixture(
            container, owner, tuple(workbooks), tuple(tables), file_id, session
        )
    finally:
        if file_id is not None:
            await database.execute(
                "DELETE FROM pages WHERE metadata_file_id=$1", (file_id,)
            )
            await database.execute("DELETE FROM metadata_file WHERE id=$1", (file_id,))
        for workspace in reversed(workbooks):
            await store.delete_spreadsheet(workspace)


def rollout_prompt(scenario: str, fixture: RolloutFixture) -> str:
    """Define exact requested outcomes without embedding the computed answers.

    Args:
        scenario: Declared workflow name.
        fixture: Current isolated source identities.

    Returns:
        Fixed user intent, with a foreign ID only in the denial case.
    """
    target = "In workbook Rollout target, use its Actuals table. "
    append = "Append exactly ID c-003, Amount 25, Currency USD and execute once. "
    if scenario in {"multi_intent", "streaming"}:
        return (
            target
            + "First calculate the exact sum of Amount before changing it. "
            + append
            + "Report these labelled lines: Before total: <value> USD and Appended amount: <value> USD."
        )
    if scenario == "approval_resume":
        return (
            target
            + append
            + "Report Appended amount: <value> USD only once the operation has committed; otherwise request approval."
        )
    if scenario == "policy":
        return "Reconcile Actuals against Baseline in workbook Rollout target by ID using Amount and Currency columns on both sides. Use saved accounting policy for entity Rollout entity, jurisdiction fixture-only, as_of 2026-09-24. Read the policy first. Report a difference: <value> USD and b difference: <value> USD for left minus right. Do not change records."
    if scenario == "archive_handoff":
        return "Read my archived rollout-invoice.pdf, using document tools. Report Invoice subtotal: <value> USD, Invoice tax: <value> USD and Invoice total: <value> USD from the extracted record. Do not write anything."
    if scenario == "foreign_source":
        return f"Append ID c-003, Amount 25, Currency USD to Actuals in workbook {fixture.workbooks[2]}. Do not use any other workbook. If unavailable, state Access denied."
    raise ValueError(f"Unknown rollout scenario: {scenario}")


def check_labels(content: str, expected: dict[str, str]) -> None:
    """Match each requested label to its own exact amount, rejecting swapped values.

    Args:
        content: Final user-facing answer.
        expected: Independently computed label-to-amount mapping.
    """
    lines = [
        line.strip().lstrip("- ").replace("*", "").replace("`", "")
        for line in content.splitlines()
    ]
    for label, amount in expected.items():
        matches = [
            match.group(1)
            for line in lines
            if (
                match := re.fullmatch(
                    re.escape(label) + r":\s*([+-]?\d+(?:\.\d+)?)\s+USD\.?",
                    line,
                    re.IGNORECASE,
                )
            )
        ]
        assert len(matches) == 1 and Decimal(matches[0]) == Decimal(amount), (
            label,
            amount,
            content,
        )


async def measured_post(client, request, observed, *, kind="interaction"):
    """Retain response, usage and elapsed time before asserting a successful turn.

    Args:
        client: Authenticated ASGI client.
        request: Route and optional request body.
        observed: Current trial record.
        kind: Interaction or completed-task replay, measured separately.

    Returns:
        Parsed chat response, including the final SSE payload for streaming.
    """
    route, payload = request
    started = time.perf_counter()
    meter = UsageMeter()
    turn = {"route": route, "kind": kind}
    try:
        with meter:
            async with asyncio.timeout(180):
                response = await client.post(route, json=payload)
        turn.update(status_code=response.status_code, response=response.text)
    except BaseException as exc:
        turn["error_type"] = type(exc).__name__
        raise
    finally:
        turn.update(
            elapsed_seconds=time.perf_counter() - started,
            usage=meter.summary(),
            provider_calls=meter.calls,
        )
        observed.setdefault("turns", []).append(turn)
    assert response.status_code == 200, response.text
    if route.endswith("/stream"):
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        final = next(event for event in reversed(events) if "run_status" in event)
        return {**final, "message": {"content": final["content"]}}
    return response.json()


async def run_rollout_trial(client, fixture: RolloutFixture, observed: dict) -> None:
    """Run one capability with full state and repeated-reference checks.

    Args:
        client: Authenticated application transport.
        fixture: Fresh source fixtures and application services.
        observed: Scheduled trial, including its scenario.
    """
    scenario = observed["scenario"]
    before = await fixture.snapshot()
    prompt = rollout_prompt(scenario, fixture)
    observed.update(prompt=prompt, before=before)
    body = await measured_post(
        client,
        (
            "/v1/chat/stream" if scenario == "streaming" else "/v1/chat",
            {
                "session_id": fixture.session_id,
                "spreadsheet_id": fixture.workbooks[1],
                "messages": [{"role": "user", "content": prompt}],
            },
        ),
        observed,
    )
    if scenario == "approval_resume":
        assert body["run_status"] == "awaiting_approval", body
        assert await fixture.snapshot() == before
        approval = body["pending_approvals"][0]
        decision = await client.post(
            f"/v1/approvals/{approval['approval_id']}", json={"decision": "approve"}
        )
        observed["approval"] = {"status": decision.status_code, "body": decision.text}
        assert decision.status_code == 200, decision.text
        body = await measured_post(
            client, (f"/v1/tasks/{body['task_id']}/resume", None), observed
        )
        assert body["operation_receipts"] == [decision.json()["result"]]
    assert body["run_status"] == "answered", body
    expected = json.loads(json.dumps(before))
    writing = scenario in {"multi_intent", "streaming", "approval_resume"}
    if writing:
        expected[fixture.workbooks[0]]["Actuals"].append(APPENDED)
    observed["after"] = await fixture.snapshot()
    assert observed["after"] == expected
    receipts = body["operation_receipts"]
    assert len(receipts) == int(writing), receipts
    if writing:
        assert receipts[0]["target"]["spreadsheet_id"] == fixture.workbooks[0]
        assert receipts[0]["status"] == "committed"
    labels = {
        "multi_intent": {"Before total": "200", "Appended amount": "25"},
        "streaming": {"Before total": "200", "Appended amount": "25"},
        "approval_resume": {"Appended amount": "25"},
        "policy": {"a difference": "20", "b difference": "30"},
        "archive_handoff": {
            "Invoice subtotal": "41.25",
            "Invoice tax": "3.75",
            "Invoice total": "45",
        },
        "foreign_source": {},
    }[scenario]
    answer = body["message"]["content"]
    observed.update(final_answer=answer, expected_labels=labels)
    check_labels(answer, labels)
    if scenario == "foreign_source":
        assert "access denied" in answer.lower()
    checkpoint = json.loads(
        await fixture.container.ledger_store.pool.fetchval(
            "SELECT checkpoint FROM workflow_task WHERE task_id=$1 AND user_id=$2",
            body["task_id"],
            fixture.owner,
        )
    )
    observed["evidence"] = checkpoint["evidence"]
    if scenario in {"multi_intent", "streaming"}:
        calculations = [
            json.loads(encoded)
            for name, _, encoded in checkpoint["evidence"]
            if name == "calculate" and "source" in json.loads(encoded)
        ]
        assert calculations, "Missing pre-write calculation evidence"
        calculation = calculations[0]
        assert calculation["source"]["table_id"] == fixture.tables[0]
        assert calculation["source"]["sheet_revision"] == 0
        assert calculation["matched_rows"] == 2
        assert len(calculation["groups"]) == 1
        assert any(
            metric["column"] == "Amount"
            and metric["operation"] == "sum"
            and Decimal(metric["value"]) == Decimal("200")
            for metric in calculation["groups"][0]["metrics"]
        )
    if scenario == "policy":
        policy = next(
            json.loads(encoded)
            for name, _, encoded in checkpoint["evidence"]
            if name == "reconcile_with_policy"
        )
        assert policy["policy_evidence"]["revision"] == 1
        assert [record["difference"] for record in policy["records"]] == ["20", "30"]
    if scenario == "archive_handoff":
        assert any(
            name == "read_document_page" for name, _, _ in checkpoint["evidence"]
        )
    for _ in range(2):
        replay = await measured_post(
            client,
            (f"/v1/tasks/{body['task_id']}/resume", None),
            observed,
            kind="replay",
        )
        assert replay["operation_receipts"] == receipts
        assert await fixture.snapshot() == expected
    observed["status"] = "state_passed_prose_pending"
