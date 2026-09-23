"""Financial reads use one owned snapshot and retain revision-labelled evidence."""

import uuid

import pytest

from app.services.catalogue.service import CatalogueService
from klaudia.core.agent.context import TaskContext
from klaudia.core.agent.tools import DiscoveryTools
from ledger.catalogue import CatalogueStore
from ledger.errors import RevisionConflictError
from ledger.financial_contracts import CheckedFinancialRequest
from ledger.resources import ResourceNotFoundError, TableRegistration
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def financial_setup():
    """Create two owned source workbooks and clean up only those fixtures."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    catalogue = CatalogueStore(store.pool)
    workbooks, tables = [], []
    try:
        for amount in ("1.123456789012345678901", "1.1"):
            workbook = await store.create_spreadsheet(
                90211, f"financial-{uuid.uuid4().hex}"
            )
            workspace = workbook["spreadsheetId"]
            workbooks.append(workspace)
            sheet = await store.create_sheet(
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
                        grain="one row per invoice",
                    ),
                )
            )
        yield store, catalogue, workbooks, tables
    finally:
        for workspace in workbooks:
            await store.delete_spreadsheet(workspace)
        await store.close()


def reconciliation(tables):
    """Bind a full outer comparison to both observed table revisions."""
    return CheckedFinancialRequest.model_validate(
        {
            "table_id": tables[0]["table_id"],
            "sources": [
                {
                    "table_id": table["table_id"],
                    "sheet_revision": 0,
                    "catalogue_revision": 1,
                }
                for table in tables
            ],
            "query": {
                "operation": "reconcile",
                "right_table_id": tables[1]["table_id"],
                "left_keys": ["ID"],
                "right_keys": ["ID"],
                "null_keys": "reject",
                "left_amount": "Amount",
                "right_amount": "Amount",
                "tolerance": "0",
                "policies": {
                    "numeric_text": "decimal",
                    "null_amounts": "reject",
                    "unit": "USD",
                    "left_unit_column": "Currency",
                    "right_unit_column": "Currency",
                },
            },
        }
    )


async def test_cross_workbook_financial_evidence_is_exact_and_column_labelled(
    financial_setup,
):
    """An owned two-source comparison retains decimal digits and stable columns."""
    _, catalogue, workbooks, tables = financial_setup
    evidence = await catalogue.financial_owned(90211, reconciliation(tables))
    assert evidence["records"][0]["difference"] == "0.023456789012345678901"
    assert [source["spreadsheet_id"] for source in evidence["sources"]] == workbooks
    assert evidence["sources"][0]["columns"] == [
        {"name": column["name"], "column_id": column["column_id"]}
        for column in tables[0]["columns"]
    ]
    assert "sources" not in evidence["query"]


async def test_either_foreign_source_rejects_the_whole_query(financial_setup):
    """No partial records or counts escape when a right workbook is foreign."""
    store, catalogue, workbooks, tables = financial_setup
    query = reconciliation(tables)
    with pytest.raises(ResourceNotFoundError, match="Table not found"):
        await catalogue.financial_owned(90212, query)
    await store.pool.execute(
        "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
        90212,
        workbooks[1],
    )
    with pytest.raises(ResourceNotFoundError, match="Table not found"):
        await catalogue.financial_owned(90211, query)


async def test_changed_right_source_or_catalogue_blocks_comparison(financial_setup):
    """Both source revisions are checked before any financial evidence returns."""
    store, catalogue, _, tables = financial_setup
    query = reconciliation(tables)
    await store.pool.execute(
        "UPDATE ledger_resource SET revision = revision + 1 WHERE resource_id = $1",
        tables[1]["table_id"],
    )
    with pytest.raises(RevisionConflictError):
        await catalogue.financial_owned(90211, query)
    await store.pool.execute(
        "UPDATE ledger_resource SET revision = revision - 1 WHERE resource_id = $1",
        tables[1]["table_id"],
    )
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = grid WHERE sheet_id = $1", tables[1]["sheet_id"]
    )
    with pytest.raises(RevisionConflictError):
        await catalogue.financial_owned(90211, query)


async def test_financial_tool_requires_both_inspections_and_invalidates_stale_refs(
    financial_setup,
):
    """The task supplies revisions; the model supplies only identities and intent."""
    store, catalogue, _, tables = financial_setup
    discovery = DiscoveryTools(CatalogueService(catalogue), TaskContext(user_id=90211))
    tools = {tool.name: tool for tool in discovery.tools}
    arguments = variance_arguments(tables)
    await tools["inspect_resource"].ainvoke({"table_id": tables[0]["table_id"]})
    with pytest.raises(ValueError, match="Inspect"):
        await tools["financial_query"].ainvoke(arguments)
    await tools["inspect_resource"].ainvoke({"table_id": tables[1]["table_id"]})
    evidence = await tools["financial_query"].ainvoke(arguments)
    assert evidence["records"][0]["status"] == "compared"
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = grid WHERE sheet_id = $1", tables[1]["sheet_id"]
    )
    with pytest.raises(RevisionConflictError):
        await tools["financial_query"].ainvoke(arguments)
    assert discovery.working_set == ()


async def test_financial_record_output_budget_never_truncates_silently(financial_setup):
    """Large selected cells fail the byte budget instead of returning a partial row."""
    store, catalogue, _, tables = financial_setup
    table = tables[0]
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = jsonb_set(grid, '{1,1}', to_jsonb($1::text)) WHERE sheet_id = $2",
        "x" * 70000,
        table["sheet_id"],
    )
    await store.pool.execute(
        "UPDATE ledger_resource SET source_revision = 1 WHERE resource_id = $1",
        table["table_id"],
    )
    query = CheckedFinancialRequest.model_validate(
        {
            "table_id": table["table_id"],
            "sources": [
                {
                    "table_id": table["table_id"],
                    "sheet_revision": 1,
                    "catalogue_revision": 1,
                }
            ],
            "query": {"operation": "records", "columns": ["Amount"]},
        }
    )
    with pytest.raises(ValueError, match="65536"):
        await catalogue.financial_owned(90211, query)


async def test_record_pages_keep_postgres_numeric_cells_exact(financial_setup):
    """JSONB fractional values never pass through a float on record reads."""
    store, catalogue, _, tables = financial_setup
    table = tables[0]
    await store.pool.execute(
        "UPDATE ledger_sheet SET grid = jsonb_set(grid, '{1,1}', $1::jsonb) WHERE sheet_id = $2",
        "0.123456789012345678901",
        table["sheet_id"],
    )
    await store.pool.execute(
        "UPDATE ledger_resource SET source_revision = 1 WHERE resource_id = $1",
        table["table_id"],
    )
    query = CheckedFinancialRequest.model_validate(
        {
            "table_id": table["table_id"],
            "sources": [
                {
                    "table_id": table["table_id"],
                    "sheet_revision": 1,
                    "catalogue_revision": 1,
                }
            ],
            "query": {"operation": "records", "columns": ["Amount"]},
        }
    )
    evidence = await catalogue.financial_owned(90211, query)
    assert evidence["records"][0]["values"]["Amount"] == {
        "type": "number",
        "value": "0.123456789012345678901",
    }


async def test_source_revision_bindings_are_complete_and_unique(financial_setup):
    """An application caller cannot omit or duplicate a source observation."""
    _, catalogue, _, tables = financial_setup
    query = reconciliation(tables)
    for sources in ([query.sources[0]], [query.sources[0], query.sources[0]]):
        with pytest.raises(ValueError, match="exactly once"):
            await catalogue.financial_owned(
                90211, query.model_copy(update={"sources": sources})
            )


async def test_self_join_uses_one_source_observation(financial_setup):
    """The same table can play both roles without duplicate revision claims."""
    _, catalogue, _, tables = financial_setup
    query = reconciliation([tables[0], tables[0]])
    query = query.model_copy(update={"sources": query.sources[:1]})
    evidence = await catalogue.financial_owned(90211, query)
    assert len(evidence["sources"]) == 1
    assert evidence["status_counts"] == {"matched": 1}


async def test_scripted_main_agent_retains_financial_evidence(financial_setup):
    """The full agent loop preserves labelled two-source query evidence."""
    from langchain_core.messages import AIMessage
    from klaudia.core.agent.agent import MainAgent
    from tests.unit.test_main_agent import ScriptedModel, call

    _, catalogue, _, tables = financial_setup
    model = ScriptedModel(
        [
            call("load_skill", {"name": "financial-execution"}, "skill"),
            call("inspect_resource", {"table_id": tables[0]["table_id"]}, "left"),
            call("inspect_resource", {"table_id": tables[1]["table_id"]}, "right"),
            call(
                "financial_query",
                variance_arguments(tables),
                "query",
            ),
            AIMessage(content="The amount difference is 0.023456789012345678901 USD."),
        ]
    )
    outcome = await MainAgent(model, CatalogueService(catalogue)).run(
        "Compare the amounts", TaskContext(user_id=90211)
    )
    assert outcome.status == "answered"
    assert outcome.tools_called[-1] == "financial_query"
    assert outcome.tool_evidence[-1][0] == "financial_query"
    assert len(outcome.working_set) == 2
    assert outcome.operation_receipts == ()


def variance_arguments(tables):
    """Exercise generic two-source reads without bypassing saved reconciliation policy."""
    arguments = reconciliation(tables).model_dump(mode="json", exclude={"sources"})
    query = arguments["query"]
    query.pop("tolerance")
    query.update(
        operation="variance",
        direction="left_minus_right",
        zero_baseline="null",
        percentage_places=2,
        rounding="ROUND_HALF_EVEN",
    )
    return arguments
