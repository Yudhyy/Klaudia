"""The shared sandbox runner grades real catalogue, calculation and write evidence."""

import json

import pytest
from langchain_core.messages import AIMessage

from app.services.catalogue.service import CatalogueService
from app.services.core.operations import OperationService
from klaudia.core.agent.agent import MainAgent
from ledger.catalogue import CatalogueStore
from ledger.store import LedgerStore
from tests.e2e.capability_cases import seeded_capability_case
from tests.e2e.engine_inprocess import run_case_inprocess
from tests.e2e.sut import MainAgentSUT
from tests.integration.postgres import POSTGRES_TEST_URL
from tests.unit.test_main_agent import call


class CapabilityModel:
    """Follow tool evidence without access to expected totals or resource IDs."""

    def __init__(self, scenario: str, merchant: str = "Taxi vendor") -> None:
        """Select the financial operation to exercise.

        Args:
            scenario: Sum or append behavior, with answers read only from tools.
            merchant: Literal merchant submitted by the scripted model.
        """
        self.scenario = scenario
        self.merchant = merchant

    def bind_tools(self, tools):
        """Accept the runtime's declared tool set."""
        return self

    async def ainvoke(self, messages, config=None):
        """Use observed identities, calculated values and prepared references.

        Args:
            messages: Agent conversation including tool evidence.
            config: Callback configuration forwarded by the runtime.

        Returns:
            Next tool request or evidence-backed response.
        """
        previous = messages[-1]
        if previous.type == "human":
            return call("search_resources", {"intent": "Claims"})
        evidence = json.loads(previous.content)
        if previous.name == "search_resources":
            return call(
                "inspect_resource",
                {"table_id": evidence["candidates"][0]["table_id"]},
                "inspect",
            )
        if previous.name == "inspect_resource":
            table_id = evidence["table_id"]
            if self.scenario == "sum_1000":
                return call(
                    "calculate",
                    {
                        "table_id": table_id,
                        "metrics": [{"column": "Amount", "operation": "sum"}],
                    },
                    "calculate",
                )
            return call(
                "prepare_table_append",
                {
                    "table_id": table_id,
                    "records": [
                        {
                            "Date": "2026-06-30",
                            "Merchant": self.merchant,
                            "Category": "Transport",
                            "Amount": 185000,
                        }
                    ],
                },
                "prepare",
            )
        if previous.name == "prepare_table_append":
            return call(
                "execute_operation",
                {"operation_ref": evidence["operation_ref"]},
                "execute",
            )
        if previous.name == "calculate":
            total = int(evidence["groups"][0]["metrics"][0]["value"])
            return AIMessage(content=f"Amount: {total:,}")
        return AIMessage(content="Committed the append; formulas are unsupported.")


@pytest.mark.parametrize("scenario", ["sum_1000", "append"])
async def test_shared_runner_grades_real_financial_evidence(scenario):
    """Both supported slices pass from real tool results and full fixture state."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    try:
        async with seeded_capability_case(store, scenario) as fixture:
            agent = MainAgent(
                CapabilityModel(scenario),
                CatalogueService(CatalogueStore(store.pool)),
                operations=OperationService(store),
            )
            records = await run_case_inprocess(
                None,
                None,
                None,
                fixture.case,
                spreadsheet_ids={"active": fixture.workbook_id},
                sut=MainAgentSUT(agent),
                observe_state=fixture.observe_state,
            )
            assert records[0].result.passed, records[0].result.reasons
            assert records[0].result.detail["ledger_state"]
            assert records[0].view.runtime == "main"
    finally:
        await store.close()


@pytest.mark.parametrize("merchant", ["Taxi vendor", "Taxi"])
async def test_append_diagnostics_locate_literal_mismatch_before_persistence(merchant):
    """Submitted values survive preparation and execution; altered intent still fails."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    try:
        async with seeded_capability_case(store, "append") as fixture:
            agent = MainAgent(
                CapabilityModel("append", merchant),
                CatalogueService(CatalogueStore(store.pool)),
                operations=OperationService(store),
            )
            records = await run_case_inprocess(
                None,
                None,
                None,
                fixture.case,
                spreadsheet_ids={"active": fixture.workbook_id},
                sut=MainAgentSUT(agent),
                observe_state=fixture.observe_state,
            )
            observation = records[0].view.append_attempts[0]
            proposal = json.loads(
                await store.pool.fetchval(
                    "SELECT request_payload FROM ledger_table_operation WHERE user_id = $1 AND idempotency_key = $2",
                    fixture.case.turns[0].as_user,
                    observation["operation_ref"],
                )
            )
            assert observation["arguments"]["records"][0]["Merchant"] == merchant
            assert proposal["records"] == observation["arguments"]["records"]
            assert records[0].view.ledger_state["Claims"][-1][1] == merchant
            assert records[0].result.passed == (merchant == "Taxi vendor")
            assert records[0].view.operation_receipts[0]["status"] == "committed"
    finally:
        await store.close()
