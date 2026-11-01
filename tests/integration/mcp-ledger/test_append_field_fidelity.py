"""Scripted boundary checks for exact field values, not live-model interpretation."""

from app.adapters.agent.operations import OperationAdapter

from app.adapters.agent.catalogue import CatalogueAdapter

from copy import deepcopy
import json

import pytest
from langchain_core.messages import AIMessage

from app.services.catalogue.service import CatalogueService
from app.services.core.operations import OperationService
from klaudia.core.agent.agent import MainAgent
from ledger.catalogue import CatalogueStore
from ledger.operations import CellValue
from ledger.store import LedgerStore
from tests.e2e.capability_cases import seeded_capability_case
from tests.e2e.engine_inprocess import run_case_inprocess
from tests.e2e.sut import MainAgentSUT
from tests.integration.postgres import POSTGRES_TEST_URL
from tests.unit.test_main_agent import ScriptedModel, call


class RecordAppendModel(ScriptedModel):
    """Submit a controlled record while using actual discovery and receipt evidence."""

    def __init__(self, record: dict[str, CellValue]) -> None:
        """Keep scripted input separate from the fixture's expected record.

        Args:
            record: Exact arguments to submit, including deliberate test mutations.
        """
        super().__init__(
            [
                call("search_resources", {"intent": "Claims"}),
                AIMessage(content="Committed the append; formulas are unsupported."),
            ]
        )
        self.record = deepcopy(record)

    async def ainvoke(self, messages, config=None):
        """Use the inspected table identity and otherwise follow the shared script.

        Args:
            messages: Real tool observations from the optional agent.
            config: Callback configuration supplied by the adapter.

        Returns:
            The next scripted tool call or final response.
        """
        previous = messages[-1]
        if previous.type == "tool" and previous.name == "search_resources":
            table_id = json.loads(previous.content)["candidates"][0]["table_id"]
            return call("inspect_resource", {"table_id": table_id}, "inspect")
        if previous.type == "tool" and previous.name == "inspect_resource":
            table_id = json.loads(previous.content)["table_id"]
            return call(
                "prepare_table_append",
                {"table_id": table_id, "records": [self.record]},
                "prepare",
            )
        if previous.type == "tool" and previous.name == "prepare_table_append":
            reference = json.loads(previous.content)["operation_ref"]
            return call("execute_operation", {"operation_ref": reference}, "execute")
        return await super().ainvoke(messages, config)


@pytest.mark.parametrize(
    "field,expected_value,submitted_value",
    [
        pytest.param(
            "Merchant",
            "O'Neil & Sons, Ltd. / West",
            "O'Neil & Sons, Ltd. / West",
            id="punctuation-preserved",
        ),
        pytest.param(
            "Merchant",
            "O'Neil & Sons, Ltd. / West",
            "ONeil and Sons Ltd West",
            id="punctuation-rewritten",
        ),
        pytest.param(
            "Merchant",
            "  North  Division\tA  ",
            "  North  Division\tA  ",
            id="whitespace-preserved",
        ),
        pytest.param(
            "Merchant",
            "  North  Division\tA  ",
            "North Division A",
            id="whitespace-collapsed",
        ),
        pytest.param(
            "Merchant", "Cafe\u0301 North", "Cafe\u0301 North", id="unicode-preserved"
        ),
        pytest.param(
            "Merchant", "Cafe\u0301 North", "Caf\u00e9 North", id="unicode-normalised"
        ),
        pytest.param("Category", "000073", "000073", id="text-code-preserved"),
        pytest.param("Category", "000073", 73, id="text-code-coerced"),
        pytest.param("Amount", -185000.25, -185000.25, id="signed-fraction-preserved"),
        pytest.param("Amount", -185000.25, 185000.25, id="sign-lost"),
        pytest.param("Amount", 0, 0, id="zero-preserved"),
        pytest.param("Amount", 0, False, id="zero-replaced-by-boolean"),
        pytest.param("Amount", 185000, "185000", id="number-replaced-by-text"),
        pytest.param("Amount", 0, None, id="zero-replaced-by-null"),
    ],
)
async def test_field_values_survive_proposal_commit_and_replay(
    field, expected_value, submitted_value
):
    """The runtime preserves submitted values; grading rejects altered intent."""
    expected_record = {
        "Date": "2026-06-30",
        "Merchant": "Taxi vendor",
        "Category": "Transport",
        "Amount": 185000,
    }
    expected_record[field] = expected_value
    submitted_record = {**expected_record, field: submitted_value}
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    try:
        async with seeded_capability_case(store, "append") as fixture:
            turn = fixture.case.turns[0]
            turn.user = (
                "Append this exact record, preserving its values and types: "
                + json.dumps(expected_record)
            )
            headers = turn.expect.ledger_state["Claims"][0]
            turn.expect.ledger_state["Claims"][-1] = [
                expected_record[column] for column in headers
            ]
            operations = OperationService(store)
            agent = MainAgent(
                RecordAppendModel(submitted_record),
                CatalogueAdapter(CatalogueService(CatalogueStore(store.pool))),
                operations=OperationAdapter(operations),
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
            record = records[0]
            attempt = record.view.append_attempts[0]
            proposal = json.loads(
                await store.pool.fetchval(
                    "SELECT request_payload FROM ledger_table_operation WHERE user_id = $1 AND idempotency_key = $2",
                    turn.as_user,
                    attempt["operation_ref"],
                )
            )
            submitted_json = json.dumps([submitted_record], sort_keys=True)
            assert (
                json.dumps(attempt["arguments"]["records"], sort_keys=True)
                == submitted_json
            )
            assert json.dumps(proposal["records"], sort_keys=True) == submitted_json
            submitted_row = [submitted_record[column] for column in headers]
            assert json.dumps(record.view.ledger_state["Claims"][-1]) == json.dumps(
                submitted_row
            )
            matches_request = json.dumps(submitted_record) == json.dumps(
                expected_record
            )
            assert record.result.passed == matches_request, record.result.reasons
            if not matches_request:
                assert record.result.detail["ledger_state"] is False
            receipt = record.view.operation_receipts[0]
            assert receipt["status"] == "committed"
            assert (
                await operations.execute(turn.as_user, attempt["operation_ref"])
                == receipt
            )
            assert json.dumps(
                await fixture.observe_state(), sort_keys=True
            ) == json.dumps(record.view.ledger_state, sort_keys=True)
    finally:
        await store.close()
