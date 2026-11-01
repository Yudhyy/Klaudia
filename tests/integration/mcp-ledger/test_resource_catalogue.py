"""Resource identity, freshness and discovery within a bound workbook."""

from app.adapters.agent.catalogue import CatalogueAdapter

import asyncio
import uuid
from datetime import date

import pytest

from ledger.catalogue import CatalogueStore
from ledger.resources import (
    ResourceExistsError,
    ResourceNotFoundError,
    ResourceSearch,
    TableRegistration,
    TableUpdate,
)
from ledger.errors import RevisionConflictError, SheetNotFoundError
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def catalogue_setup():
    """Create an isolated workbook containing two distinct table regions."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    workbook = await store.create_spreadsheet(90201, f"catalogue-{uuid.uuid4().hex}")
    workspace = workbook["spreadsheetId"]
    sheet = await store.create_sheet(
        workspace,
        "Finance",
        [
            ["Date", "Employee", "Amount"],
            ["2026-09-09", "A", 185000],
            ["2026-09-10", "B", 90000],
            [],
            [],
            ["Invoice", "Customer", "Balance"],
            ["INV-1", "Client", 100000],
            ["INV-2", "Client", 200000],
        ],
    )
    try:
        yield store, CatalogueStore(store.pool), workspace, sheet["sheetId"]
    finally:
        await store.delete_spreadsheet(workspace)
        await store.close()


def registration(sheet_id: int, **changes) -> TableRegistration:
    """Build the employee-claims definition with optional test overrides."""
    fields = dict(
        sheet_id=sheet_id,
        expected_sheet_revision=0,
        table_range="A1:C3",
        name="Employee claims",
        description="Employee travel and taxi reimbursements",
        grain="one row per employee expense claim",
        aliases=["team rides"],
        entity="Jakarta",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
    )
    return TableRegistration(**(fields | changes))


async def test_two_tables_have_stable_distinct_identities(catalogue_setup):
    """A worksheet can expose two independently addressable semantic tables."""
    _, catalogue, workspace, sheet_id = catalogue_setup
    claims = await catalogue.register(workspace, registration(sheet_id))
    invoices = await catalogue.register(
        workspace,
        registration(
            sheet_id,
            table_range="A6:C8",
            name="Receivables",
            description="Customer invoices awaiting payment",
            grain="one row per invoice",
            aliases=[],
        ),
    )
    assert claims["table_id"] != invoices["table_id"]
    assert claims["record_count"] == 2
    assert [column["name"] for column in claims["columns"]] == [
        "Date",
        "Employee",
        "Amount",
    ]
    assert (
        len(
            {
                column["column_id"]
                for table in (claims, invoices)
                for column in table["columns"]
            }
        )
        == 6
    )
    assert (await catalogue.inspect(workspace, claims["table_id"])) == claims


async def test_rename_and_metadata_update_preserve_ids(catalogue_setup):
    """Names change without replacing table or column identity."""
    store, catalogue, workspace, sheet_id = catalogue_setup
    original = await catalogue.register(workspace, registration(sheet_id))
    await store.rename_sheet(workspace, "Finance", "Operating expenses")
    stale = await catalogue.inspect(workspace, original["table_id"])
    assert stale["sheet_name"] == "Operating expenses"
    assert stale["freshness"] == "stale"
    updated = await catalogue.update(
        workspace,
        TableUpdate(
            table_id=original["table_id"],
            expected_catalogue_revision=1,
            definition=registration(
                sheet_id, expected_sheet_revision=1, name="Travel claims"
            ),
        ),
    )
    assert updated["table_id"] == original["table_id"]
    assert updated["columns"] == original["columns"]
    assert updated["catalogue_revision"] == 2
    assert updated["freshness"] == "current"


async def test_registration_rejects_stale_source_and_duplicate_range(catalogue_setup):
    """Registration cannot describe an unobserved revision or replace a table."""
    store, catalogue, workspace, sheet_id = catalogue_setup
    await catalogue.register(workspace, registration(sheet_id))
    with pytest.raises(ResourceExistsError):
        await catalogue.register(workspace, registration(sheet_id, table_range="a1:c3"))
    await store.mutate_grid(workspace, "Finance", lambda rows: rows + [[1]])
    with pytest.raises(RevisionConflictError):
        await catalogue.register(workspace, registration(sheet_id, table_range="A6:C8"))


async def test_concurrent_metadata_updates_reject_lost_updates(catalogue_setup):
    """Only one update may consume an observed catalogue revision."""
    _, catalogue, workspace, sheet_id = catalogue_setup
    original = await catalogue.register(workspace, registration(sheet_id))
    change = TableUpdate(
        table_id=original["table_id"],
        expected_catalogue_revision=1,
        definition=registration(sheet_id, name="Renamed claims"),
    )
    outcomes = await asyncio.gather(
        catalogue.update(workspace, change),
        catalogue.update(workspace, change),
        return_exceptions=True,
    )
    assert sum(isinstance(outcome, RevisionConflictError) for outcome in outcomes) == 1


async def test_changed_headers_need_explicit_identity_remapping(catalogue_setup):
    """A source header change cannot silently reuse another column's identity."""
    store, catalogue, workspace, sheet_id = catalogue_setup
    original = await catalogue.register(workspace, registration(sheet_id))
    await store.mutate_grid(
        workspace, "Finance", lambda rows: [["Amount", "Employee", "Date"], *rows[1:]]
    )
    with pytest.raises(ValueError, match="header"):
        await catalogue.update(
            workspace,
            TableUpdate(
                table_id=original["table_id"],
                expected_catalogue_revision=1,
                definition=registration(sheet_id, expected_sheet_revision=1),
            ),
        )
    assert (await catalogue.inspect(workspace, original["table_id"]))[
        "catalogue_revision"
    ] == 1


async def test_foreign_resource_is_not_discoverable_or_inspectable(catalogue_setup):
    _, catalogue, workspace, sheet_id = catalogue_setup
    original = await catalogue.register(workspace, registration(sheet_id))
    assert (await catalogue.search("foreign", ResourceSearch(intent="taxi")))[
        "candidates"
    ] == []
    with pytest.raises(ResourceNotFoundError):
        await catalogue.inspect("foreign", original["table_id"])
    with pytest.raises(SheetNotFoundError):
        await catalogue.register("foreign", registration(sheet_id))
    with pytest.raises(ResourceNotFoundError):
        await catalogue.update(
            "foreign",
            TableUpdate(
                table_id=original["table_id"],
                expected_catalogue_revision=1,
                definition=registration(sheet_id),
            ),
        )


async def test_alias_and_concept_search_report_evidence(catalogue_setup):
    _, catalogue, workspace, sheet_id = catalogue_setup
    original = await catalogue.register(workspace, registration(sheet_id))
    aliases = await catalogue.search(workspace, ResourceSearch(intent="team rides"))
    assert aliases["candidates"][0]["table_id"] == original["table_id"]
    assert "exact alias" in aliases["candidates"][0]["reasons"]
    concepts = await catalogue.search(
        workspace,
        ResourceSearch(
            intent="record yesterday's Grab ride",
            concepts=["taxi", "reimbursement"],
            required_columns=["amount"],
            entity="Jakarta",
            on_date=date(2026, 9, 9),
        ),
    )
    assert concepts["candidates"][0]["table_id"] == original["table_id"]
    assert "required columns present" in concepts["candidates"][0]["reasons"]
    assert "confidence" not in concepts["candidates"][0]


async def test_structured_filters_do_not_relax_to_other_entities(catalogue_setup):
    _, catalogue, workspace, sheet_id = catalogue_setup
    await catalogue.register(workspace, registration(sheet_id))
    for changes in (
        {"entity": "Bali"},
        {"on_date": date(2027, 1, 1)},
        {"required_columns": ["Credit"]},
    ):
        assert (
            await catalogue.search(workspace, ResourceSearch(intent="taxi", **changes))
        )["candidates"] == []


async def test_search_marks_stale_schema_without_loading_grid(catalogue_setup):
    store, catalogue, workspace, sheet_id = catalogue_setup
    await catalogue.register(workspace, registration(sheet_id))
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = '[]' WHERE sheet_id = $1", sheet_id
    )
    candidates = (await catalogue.search(workspace, ResourceSearch(intent="taxi")))[
        "candidates"
    ]
    assert candidates[0]["freshness"] == "stale"
    assert [column["name"] for column in candidates[0]["columns"]] == [
        "Date",
        "Employee",
        "Amount",
    ]


async def test_sheet_deletion_removes_catalogue_entries(catalogue_setup):
    store, catalogue, workspace, sheet_id = catalogue_setup
    original = await catalogue.register(workspace, registration(sheet_id))
    await store.delete_sheet(workspace, "Finance")
    with pytest.raises(ResourceNotFoundError):
        await catalogue.inspect(workspace, original["table_id"])
    assert (await catalogue.search(workspace, ResourceSearch(intent="taxi")))[
        "candidates"
    ] == []


async def test_exact_punctuation_name_is_discoverable(catalogue_setup):
    """Exact identity labels remain searchable without text-search lexemes."""
    _, catalogue, workspace, sheet_id = catalogue_setup
    registered = await catalogue.register(workspace, registration(sheet_id, name="$"))
    found = await catalogue.search(workspace, ResourceSearch(intent="$"))
    assert found["candidates"][0]["table_id"] == registered["table_id"]
    assert "exact name" in found["candidates"][0]["reasons"]


async def test_search_limits_schema_and_reports_more_candidates(catalogue_setup):
    """Wide tables and candidate overflow remain explicit and bounded."""
    store, catalogue, workspace, _ = catalogue_setup
    for index in range(2):
        sheet = await store.create_sheet(
            workspace, f"Wide {index}", [[f"Column {n}" for n in range(70)]]
        )
        await catalogue.register(
            workspace,
            registration(
                sheet["sheetId"],
                table_range="A1:BR1",
                name=f"Wide claims {index}",
            ),
        )
    found = await catalogue.search(
        workspace, ResourceSearch(intent="Wide claims", limit=1)
    )
    assert found["has_more"] is True
    candidate = found["candidates"][0]
    assert candidate["column_count"] == 70
    assert len(candidate["columns"]) == 16
    assert candidate["has_more_columns"] is True


async def test_owner_discovery_spans_workbooks_without_foreign_counts(catalogue_setup):
    """Owner filtering precedes ranking and limits across all owned workbooks."""
    store, catalogue, workspace, sheet_id = catalogue_setup
    first = await catalogue.register(workspace, registration(sheet_id))
    owned = await store.create_spreadsheet(90201, f"owned-{uuid.uuid4().hex}")
    foreign = await store.create_spreadsheet(90202, f"foreign-{uuid.uuid4().hex}")
    try:
        identities = {}
        for workbook in (owned, foreign):
            sheet = await store.create_sheet(
                workbook["spreadsheetId"], "Claims", [["Employee", "Amount"]]
            )
            identities[workbook["spreadsheetId"]] = await catalogue.register(
                workbook["spreadsheetId"],
                registration(sheet["sheetId"], table_range="A1:B1"),
            )
        found = await catalogue.search_owned(
            90201, ResourceSearch(intent="Employee claims", limit=2)
        )
        assert {item["table_id"] for item in found["candidates"]} == {
            first["table_id"],
            identities[owned["spreadsheetId"]]["table_id"],
        }
        assert found["has_more"] is False
        assert (
            await catalogue.search_owned(
                90203, ResourceSearch(intent="Employee claims")
            )
        )["candidates"] == []
        foreign_id = identities[foreign["spreadsheetId"]]["table_id"]
        with pytest.raises(ResourceNotFoundError):
            await catalogue.inspect_owned(90201, foreign_id)
        inspected = await catalogue.inspect_owned(90201, first["table_id"])
        assert inspected["table_id"] == first["table_id"]
        await store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
            90202,
            workspace,
        )
        with pytest.raises(ResourceNotFoundError):
            await catalogue.inspect_owned(90201, first["table_id"])
        remaining = await catalogue.search_owned(
            90201, ResourceSearch(intent="Employee claims")
        )
        assert len(remaining["candidates"]) == 1
    finally:
        await store.delete_spreadsheet(owned["spreadsheetId"])
        await store.delete_spreadsheet(foreign["spreadsheetId"])


async def test_agent_discovery_tools_recheck_real_ownership(catalogue_setup):
    """Task tools preserve database ownership checks after selecting a resource."""
    from app.services.catalogue.service import CatalogueService
    from klaudia.core.agent.context import TaskContext
    from klaudia.core.agent.tools import DiscoveryTools
    from klaudia.core.agent.ports.errors import ResourceNotFoundError

    store, catalogue, workspace, sheet_id = catalogue_setup
    registered = await catalogue.register(workspace, registration(sheet_id))
    service = CatalogueAdapter(CatalogueService(catalogue))
    owner = DiscoveryTools(
        service, TaskContext(user_id=90201, active_workbook_id="untrusted-ui-hint")
    )
    stranger = DiscoveryTools(service, TaskContext(user_id=90202))
    found = await owner.tools[0].ainvoke({"intent": "claims"})
    assert found["candidates"][0]["table_id"] == registered["table_id"]
    assert owner.working_set == ()
    await owner.tools[1].ainvoke({"table_id": registered["table_id"]})
    assert owner.working_set[0].spreadsheet_id == workspace
    with pytest.raises(ResourceNotFoundError):
        await stranger.tools[1].ainvoke({"table_id": registered["table_id"]})
    assert stranger.working_set == ()
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
        90202,
        workspace,
    )
    with pytest.raises(ResourceNotFoundError):
        await owner.tools[1].ainvoke({"table_id": registered["table_id"]})
    assert owner.working_set == ()


async def test_main_agent_discovers_and_inspects_real_catalogue(catalogue_setup):
    """A scripted model exercises the full loop against owned catalogue records."""
    import json

    from langchain_core.messages import AIMessage

    from app.services.catalogue.service import CatalogueService
    from klaudia.core.agent.agent import MainAgent
    from klaudia.core.agent.context import TaskContext

    class DiscoveryModel:
        """Choose inspection from actual search evidence rather than a fixture ID."""

        def bind_tools(self, tools):
            """Return this tool-capable test model."""
            return self

        async def ainvoke(self, messages, config=None):
            """Search, inspect, calculate and report the observed metric evidence."""
            if messages[-1].type == "human":
                name, arguments = "search_resources", {"intent": "taxi claims"}
            elif messages[-1].name == "search_resources":
                candidate = json.loads(messages[-1].content)["candidates"][0]
                name, arguments = (
                    "inspect_resource",
                    {"table_id": candidate["table_id"]},
                )
            elif messages[-1].name == "inspect_resource":
                inspected = json.loads(messages[-1].content)
                name, arguments = (
                    "calculate",
                    {
                        "table_id": inspected["table_id"],
                        "metrics": [{"column": "Amount", "operation": "sum"}],
                    },
                )
            else:
                metric = json.loads(messages[-1].content)["groups"][0]["metrics"][0]
                return AIMessage(
                    content=f"{metric['operation']} of {metric['column']}: {metric['value']}"
                )
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": arguments, "id": name, "type": "tool_call"}
                ],
            )

    _, catalogue, workspace, sheet_id = catalogue_setup
    registered = await catalogue.register(workspace, registration(sheet_id))
    outcome = await MainAgent(
        DiscoveryModel(), CatalogueAdapter(CatalogueService(catalogue))
    ).run(
        "Find the taxi claims table and sum its Amount column",
        TaskContext(user_id=90201),
    )
    assert outcome.status == "answered"
    assert outcome.tools_called == ("search_resources", "inspect_resource", "calculate")
    assert outcome.content == "sum of Amount: 275000"
    assert outcome.working_set[0].table_id == registered["table_id"]


async def test_registered_calculation_uses_owned_bounds_and_labelled_evidence(
    catalogue_setup,
):
    """The selected table excludes neighbouring records and identifies its metrics."""
    from ledger.calculations import CheckedCalculation

    _, catalogue, workspace, sheet_id = catalogue_setup
    table = await catalogue.register(workspace, registration(sheet_id))
    request = CheckedCalculation(
        table_id=table["table_id"],
        expected_sheet_revision=0,
        expected_catalogue_revision=1,
        metrics=[{"column": "Amount", "operation": "sum"}],
    )
    evidence = await catalogue.calculate_owned(90201, request)
    assert evidence["groups"][0]["metrics"][0]["value"] == "275000"
    assert (
        evidence["groups"][0]["metrics"][0]["column_id"]
        == table["columns"][2]["column_id"]
    )
    assert evidence["source"]["range"] == "A1:C3"
    assert evidence["source"]["sheet_revision"] == 0
    assert evidence["source"]["catalogue_revision"] == 1
    assert evidence["source"]["table_id"] == table["table_id"]
    assert evidence["query"]["metrics"] == [{"column": "Amount", "operation": "sum"}]
    assert "grid" not in evidence
    with pytest.raises(ResourceNotFoundError):
        await catalogue.calculate_owned(90202, request)


async def test_calculation_rejects_stale_sheet_and_catalogue(catalogue_setup):
    """An observation cannot silently calculate against changed data or bounds."""
    from ledger.calculations import CheckedCalculation

    store, catalogue, workspace, sheet_id = catalogue_setup
    table = await catalogue.register(workspace, registration(sheet_id))
    request = CheckedCalculation(
        table_id=table["table_id"],
        expected_sheet_revision=0,
        expected_catalogue_revision=1,
        metrics=[{"column": "Amount", "operation": "sum"}],
    )
    await catalogue.update(
        workspace,
        TableUpdate(
            table_id=table["table_id"],
            expected_catalogue_revision=1,
            definition=registration(sheet_id, table_range="A1:C2"),
        ),
    )
    with pytest.raises(RevisionConflictError):
        await catalogue.calculate_owned(90201, request)
    fresh = request.model_copy(update={"expected_catalogue_revision": 2})
    assert (await catalogue.calculate_owned(90201, fresh))["groups"][0]["metrics"][0][
        "value"
    ] == "185000"
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = grid WHERE sheet_id = $1", sheet_id
    )
    with pytest.raises(RevisionConflictError):
        await catalogue.calculate_owned(90201, fresh)
    with pytest.raises(RevisionConflictError):
        await catalogue.calculate_owned(
            90201, fresh.model_copy(update={"expected_sheet_revision": 1})
        )
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = 90202 WHERE spreadsheet_id = $1",
        workspace,
    )
    with pytest.raises(ResourceNotFoundError):
        await catalogue.calculate_owned(90201, fresh)


async def test_calculation_preserves_postgres_fractional_digits(catalogue_setup):
    """The database-to-calculation path never rounds a stored decimal through float."""
    from ledger.calculations import CheckedCalculation

    store, catalogue, workspace, sheet_id = catalogue_setup
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = $1::jsonb WHERE sheet_id = $2",
        '[["Amount"], [0.123456789012345678901], [0.1]]',
        sheet_id,
    )
    table = await catalogue.register(
        workspace,
        registration(sheet_id, expected_sheet_revision=1, table_range="A1:A3"),
    )
    evidence = await catalogue.calculate_owned(
        90201,
        CheckedCalculation(
            table_id=table["table_id"],
            expected_sheet_revision=1,
            expected_catalogue_revision=1,
            metrics=[{"column": "Amount", "operation": "sum"}],
        ),
    )
    assert evidence["groups"][0]["metrics"][0]["value"] == "0.223456789012345678901"
