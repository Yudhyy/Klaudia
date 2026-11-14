"""Seeded candidate-runtime cases with independent full-workbook state checks."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Literal
from uuid import uuid4

from ledger_postgres.catalogue import CatalogueStore
from ledger_core.resources import TableRegistration
from ledger_postgres.store import LedgerStore
from tests.e2e.schema import Case, Expect, MetricExpectation, Turn
from tests.e2e.synthetic import build_bulk_ledger

CAPABILITY_SEED = 1013
CAPABILITY_USER = 90501
Scenario = Literal["sum_1000", "append"]


@dataclass
class CapabilityFixture:
    """Own only the workbook created for one sandbox scenario."""

    store: LedgerStore
    workbook_id: str
    case: Case

    async def observe_state(self) -> dict:
        """Read all sheets, including unexpected creations, outside the model context.

        Returns:
            Exact fixture workbook grids keyed by current sheet title.
        """
        titles = await self.store.pool.fetch(
            "SELECT title FROM ledger_sheet WHERE workspace = $1 ORDER BY sheet_id",
            self.workbook_id,
        )
        return {
            row["title"]: await self.store.get_grid(self.workbook_id, row["title"])
            for row in titles
        }


@asynccontextmanager
async def seeded_capability_case(
    store: LedgerStore, scenario: Scenario, *, user_id: int = CAPABILITY_USER
) -> AsyncIterator[CapabilityFixture]:
    """Create an isolated registered table and remove only this fixture on exit.

    Args:
        store: Connected sandbox ledger, never a development store.
        scenario: Exact aggregation or checked append case.
        user_id: Isolated fixture owner supplied by comparison runs when needed.

    Yields:
        Case expectations and a database state observer.
    """
    if scenario not in ("sum_1000", "append"):
        raise ValueError("Unknown capability scenario")
    generated = build_bulk_ledger(
        seed=CAPABILITY_SEED, sheet="Claims", rows=1000 if scenario == "sum_1000" else 3
    )
    values = [
        ["Date", "Merchant", "Category", "Amount"],
        *generated.grids["Claims"][1:],
    ]
    workbook = await store.create_spreadsheet(user_id, f"capability-{uuid4().hex}")
    workbook_id = workbook["spreadsheetId"]
    try:
        sheet = await store.create_sheet(workbook_id, "Claims", values)
        table = await CatalogueStore(store.pool).register(
            workbook_id,
            TableRegistration(
                sheet_id=sheet["sheetId"],
                expected_sheet_revision=0,
                table_range=f"A1:D{len(values)}",
                name="Claims",
            ),
        )
        expected = Expect(ledger_state={"Claims": values})
        if scenario == "sum_1000":
            total = generated.all_sheets_total
            message = "Find Claims and calculate the sum of its Amount column. Report the Amount total."
            expected.capabilities_all = [
                "discover_resources",
                "inspect_resource",
                "calculate",
            ]
            expected.metric_evidence = [
                MetricExpectation(
                    table_id=table["table_id"],
                    column="Amount",
                    operation="sum",
                    value=str(total),
                )
            ]
            expected.content_all = ["Amount"]
            expected.contains_amount = [str(total)]
        else:
            appended = ["2026-06-30", "Taxi vendor", "Transport", 185000]
            message = "Find Claims and append one record: Date 2026-06-30, Merchant Taxi vendor, Category Transport, Amount 185000. Execute the append and report its result."
            expected.capabilities_all = ["inspect_resource", "append_records"]
            expected.committed_operations_min = 1
            expected.ledger_state = {"Claims": [*values, appended]}
        case = Case(
            id=f"CAP-{scenario}",
            category="capability",
            title=scenario,
            resource_scope="owned_workbooks",
            mutating=scenario == "append",
            turns=[
                Turn(
                    user=message,
                    as_user=user_id,
                    spreadsheet="active",
                    expect=expected,
                )
            ],
        )
        yield CapabilityFixture(store, workbook_id, case)
    finally:
        await store.pool.execute(
            "DELETE FROM ledger_table_operation WHERE workspace = $1", workbook_id
        )
        await store.delete_spreadsheet(workbook_id)
