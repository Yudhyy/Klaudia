"""Opt-in live financial queries graded through saved evidence and full state."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest

from app.models.chat import KlaudiaMessage
from ledger.resources import TableRegistration
from tests.e2e.comparison_owner import comparison_owner
from tests.e2e.conftest import requires_live
from tests.e2e.sandbox import sandbox_enabled

pytestmark = [
    requires_live,
    pytest.mark.skipif(
        os.environ.get("E2E_FINANCIAL_EXECUTION") != "1",
        reason="Set E2E_FINANCIAL_EXECUTION=1 to run live financial checks",
    ),
]

OPERATIONS = ("records", "lookup", "join", "reconcile", "aging", "variance")
LEFT = [
    ["ID", "Amount", "Currency", "Due"],
    ["a", 120, "USD", "2026-09-13"],
    ["b", 80, "USD", "2026-08-14"],
    ["c", 0, "USD", "2026-08-13"],
    ["d", 50, "USD", "2026-09-14"],
]
RIGHT = [
    ["ID", "Amount", "Currency"],
    ["a", 100, "USD"],
    ["b", 0, "USD"],
    ["x", 30, "USD"],
]


def prompt(operation: str) -> str:
    """Describe a fixed business query with every required numeric convention.

    Args:
        operation: Scenario being graded.

    Returns:
        User request with explicit source and policy choices.
    """
    requests = {
        "records": "Return only the first two Actuals records, selecting ID and Amount, sorted by numeric Amount descending with nulls last. Use offset 0 and limit 2; reject numeric text. State the full matched row count.",
        "lookup": "Look up the unique Actuals record whose ID equals text a, returning ID and Amount. Missing records should return null; duplicate matches must fail.",
        "join": "Left join Actuals to Baselines by exact ID, with one_to_one cardinality and null keys rejected. Return ID and Amount from both sides, including unmatched Actuals. Use offset 0 and limit 100.",
        "reconcile": "Reconcile Actuals Amount against Baselines Amount by exact ID, with null keys rejected and absolute tolerance 0. Include both unmatched sides and show left-minus-right differences. Use offset 0 and limit 100.",
        "aging": "Age the signed outstanding Amount in Actuals using Due as the due date and 2026-09-13 as the as-of date. Use inclusive overdue upper bounds [30, 60, 90], with separate not_due and due_today buckets. right_unit_column must be null.",
        "variance": "Calculate Actuals Amount minus Baselines Amount by exact ID. Reject null keys. Use direction left_minus_right, zero_baseline null, percentage_places 2 and ROUND_HALF_EVEN. The signed baseline is Baselines Amount. Include unmatched sides. Use offset 0 and limit 100.",
    }
    if operation == "reconcile":
        return (
            "Load policy-reconciliation and read /accounting-policy.md. "
            "Use saved policy for entity Financial fixture, jurisdiction fixture-only, "
            "as_of 2026-09-13. "
            + requests[operation]
            + " Both unit columns are Currency. Use reconcile_with_policy and report its labelled evidence."
        )
    policy = " For financial amounts, numeric_text=reject, null_amounts=reject, unit=USD, left_unit_column=Currency and right_unit_column=Currency, except aging where the right column is null."
    return (
        "Load financial-execution and inspect the registered tables in this workbook. "
        + requests[operation]
        + (policy if operation in ("reconcile", "aging", "variance") else "")
        + " Use financial_query and report its labelled evidence."
    )


def check_evidence(operation: str, evidence: dict) -> None:
    """Grade independent fixture outcomes and applied policies.

    Args:
        operation: Expected capability.
        evidence: Saved native financial tool output.

    Raises:
        AssertionError: A value, label, policy or population differs.
    """
    assert evidence["operation"] == operation
    assert all(
        source["columns"] and source["sheet_revision"] == 0
        for source in evidence["sources"]
    )
    query = evidence["query"]["query"]
    if operation in ("records", "join", "reconcile", "variance"):
        assert query["offset"] == 0
        assert query["limit"] == (2 if operation == "records" else 100)
    if operation in ("records", "lookup", "join"):
        assert query["columns"] == ["ID", "Amount"]
    if operation in ("join", "reconcile", "variance"):
        assert query["left_keys"] == query["right_keys"] == ["ID"]
        assert query["null_keys"] == "reject"
    if operation in ("reconcile", "variance"):
        assert query["left_amount"] == query["right_amount"] == "Amount"
    if operation in ("reconcile", "aging", "variance"):
        assert query["policies"]["unit"] == "USD"
        assert query["policies"]["numeric_text"] == "reject"
        assert query["policies"]["null_amounts"] == "reject"
        assert query["policies"]["left_unit_column"] == "Currency"
        assert query["policies"]["right_unit_column"] == (
            None if operation == "aging" else "Currency"
        )
    if operation == "records":
        assert query["sort"] == [
            {"column": "Amount", "kind": "number", "direction": "desc", "nulls": "last"}
        ]
        assert query["numeric_text"] == "reject"
        assert query["filters"] == []
        assert evidence["matched_rows"] == 4 and evidence["next_offset"] == 2
        assert [row["values"]["Amount"]["value"] for row in evidence["records"]] == [
            "120",
            "80",
        ]
        assert [row["values"]["ID"]["value"] for row in evidence["records"]] == [
            "a",
            "b",
        ]
    elif operation == "lookup":
        assert query["missing"] == "null"
        assert query["filters"] == [{"column": "ID", "value": "a"}]
        assert evidence["matched_rows"] == 1
        assert evidence["record"]["values"]["Amount"] == {
            "type": "number",
            "value": "120",
        }
    elif operation == "join":
        assert query["join_kind"] == "left"
        assert query["cardinality"] == "one_to_one"
        assert query["right_columns"] == ["ID", "Amount"]
        assert evidence["matched_rows"] == 4
        assert [
            row["right"]["values"]["Amount"]["value"] if row["right"] else None
            for row in evidence["records"]
        ] == ["100", "0", None, None]
    elif operation == "reconcile":
        assert evidence["policy_evidence"]["revision"] == 1
        assert evidence["accounting_validation"] == "policy_parameters_checked"
        assert evidence["status_counts"] == {
            "different": 2,
            "left_only": 2,
            "right_only": 1,
        }
        assert [row["difference"] for row in evidence["records"]] == [
            "20",
            "80",
            None,
            None,
            None,
        ]
        assert query["tolerance"] == "0"
    elif operation == "variance":
        assert query["zero_baseline"] == "null"
        assert query["percentage_places"] == 2
        assert evidence["records"][0]["percentage"] == "20.00"
        assert evidence["records"][1]["percentage_status"] == "zero_baseline"
        assert evidence["status_counts"] == {
            "compared": 2,
            "left_only": 2,
            "right_only": 1,
        }
        assert query["direction"] == "left_minus_right"
        assert query["rounding"] == "ROUND_HALF_EVEN"
    else:
        assert query["as_of"] == "2026-09-13"
        assert query["bucket_days"] == [30, 60, 90]
        assert query["amount"] == "Amount"
        assert query["due_date"] == "Due"
        assert [
            (bucket["label"], bucket["amount"], bucket["record_count"])
            for bucket in evidence["buckets"]
        ] == [
            ("not_due", "50", 1),
            ("due_today", "120", 1),
            ("1_30_days", "80", 1),
            ("31_60_days", "0", 1),
            ("61_90_days", "0", 0),
            ("over_90_days", "0", 0),
        ]


@pytest.fixture(scope="module", autouse=True)
def financial_report():
    """Persist scheduled trials before setup and retain failures or unrun cases.

    Yields:
        Report containing only synthetic evidence and non-secret settings.
    """
    from config.settings import get_settings

    settings = get_settings()
    if not sandbox_enabled() or os.environ.get("E2E_SANDBOX_ACTIVE") != "1":
        raise RuntimeError("Financial smoke checks require the isolated sandbox")
    report = {
        "suite": "financial_execution",
        "model": settings.llm_model,
        "provider": settings.model_provider,
        "temperature": settings.llm_temperature,
        "disable_thinking": settings.llm_disable_thinking,
        "fixture_version": 2,
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "git_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "cases": {operation: {"status": "unrun"} for operation in OPERATIONS},
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = (
        Path(__file__).with_name("outputs")
        / f"financial-{stamp}-{uuid4().hex[:8]}.json"
    )
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    try:
        yield report
    finally:
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Financial smoke report: {path}")


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("operation", OPERATIONS)
async def test_financial_live(operation, container, orchestrator, financial_report):
    """Grade native evidence and unchanged full grids through actual main chat.

    Args:
        operation: Scheduled capability.
        container: Sandbox service container.
        orchestrator: Full chat service path.
        financial_report: Mutable report retaining trial status.
    """
    observed = financial_report["cases"][operation]
    observed["status"] = "running"
    store = container.ledger_store
    try:
        async with comparison_owner(store.pool) as user_id:
            observed["user_id"] = user_id
            workbook = await store.create_spreadsheet(
                user_id, f"financial-live-{uuid4().hex}"
            )
            workspace = workbook["spreadsheetId"]
            observed["workbook_id"] = workspace
            try:
                table_ids = {}
                for title, values, region in (
                    ("Actuals", LEFT, "A1:D5"),
                    ("Baselines", RIGHT, "A1:C4"),
                ):
                    sheet = await store.create_sheet(workspace, title, values)
                    registered = await container.catalogue._store.register(
                        workspace,
                        TableRegistration(
                            sheet_id=sheet["sheetId"],
                            expected_sheet_revision=0,
                            table_range=region,
                            name=title,
                            grain="one row per ID",
                            entity="Financial fixture",
                        ),
                    )
                    table_ids[title] = registered["table_id"]
                if operation == "reconcile":
                    from app.services.memory.contracts import DocumentEdit, DocumentPath

                    await container.memory_documents.write(
                        user_id,
                        DocumentPath.ACCOUNTING_POLICY,
                        DocumentEdit(
                            expected_revision=0,
                            content="Synthetic fixture policy",
                            policy={
                                "entity": "Financial fixture",
                                "jurisdiction": "fixture-only",
                                "effective_from": "2026-01-01",
                                "effective_until": "2026-12-31",
                                "unit": "USD",
                                "numeric_text": "reject",
                                "null_amounts": "reject",
                                "null_keys": "reject",
                                "tolerance": "0",
                            },
                        ),
                    )
                started = time.monotonic()
                response = await orchestrator.process(
                    [KlaudiaMessage(role="user", content=prompt(operation))],
                    None,
                    user_id,
                    spreadsheet_id=workspace,
                )
                observed["elapsed_seconds"] = time.monotonic() - started
                observed["response"] = response.model_dump()
                checkpoint = json.loads(
                    await store.pool.fetchval(
                        "SELECT checkpoint FROM workflow_task WHERE task_id = $1 AND user_id = $2",
                        response.task_id,
                        user_id,
                    )
                )
                evidence = [
                    json.loads(encoded)
                    for name, _, encoded in checkpoint["evidence"]
                    if name in {"financial_query", "reconcile_with_policy"}
                ]
                observed["financial_evidence"] = evidence
                observed["actual_state"] = {
                    title: (await store.get_snapshot(workspace, title)).values
                    for title in ("Actuals", "Baselines")
                }
                assert response.run_status == "answered"
                assert len(evidence) == 1
                expected_ids = [table_ids["Actuals"]]
                if operation in ("join", "reconcile", "variance"):
                    expected_ids.append(table_ids["Baselines"])
                assert [
                    source["table_id"] for source in evidence[0]["sources"]
                ] == expected_ids
                assert evidence[0]["query"]["table_id"] == table_ids["Actuals"]
                if len(expected_ids) == 2:
                    assert (
                        evidence[0]["query"]["query"]["right_table_id"]
                        == table_ids["Baselines"]
                    )
                check_evidence(operation, evidence[0])
                assert observed["actual_state"] == {"Actuals": LEFT, "Baselines": RIGHT}
                assert response.operation_receipts == []
            finally:
                await store.delete_spreadsheet(workspace)
        observed["status"] = "passed"
    except BaseException as error:
        observed["status"] = "failed"
        observed["error_type"] = type(error).__name__
        raise
