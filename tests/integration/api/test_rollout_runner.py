"""Exercise the rollout grader through real HTTP, database and task boundaries."""

import json

from langchain_core.messages import AIMessage
import pytest

from app.services.core.main_chat import MainChatService
from app.services.core.operations import OperationService
from app.services.memory.store import MemoryDocumentStore
from tests.e2e.comparison_owner import comparison_owner
from tests.e2e.rollout_cases import SCENARIOS, rollout_fixture, run_rollout_trial
from tests.integration.api.test_durable_tasks import approval_client, durable_client  # noqa: F401
from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.unit.test_main_agent import ScriptedModel, call


class RolloutModel(ScriptedModel):
    """Supply fixed intents while executing real tools and original references."""

    def __init__(self, scenario, fixture):
        """Select a deterministic script for the requested qualification case."""
        target = fixture.tables[0]
        answers = {
            "multi_intent": "Before total: 200 USD\nAppended amount: 25 USD",
            "streaming": "Before total: 200 USD\nAppended amount: 25 USD",
            "approval_resume": "Appended amount: 25 USD",
            "policy": "a difference: 20 USD\nb difference: 30 USD",
            "archive_handoff": "Invoice subtotal: 41.25 USD\nInvoice tax: 3.75 USD\nInvoice total: 45 USD",
            "foreign_source": "Access denied",
        }
        self.answer = answers[scenario]
        if scenario == "policy":
            script = [
                call(
                    "reconcile_with_policy",
                    {
                        "table_id": target,
                        "right_table_id": fixture.tables[1],
                        "left_keys": ["ID"],
                        "right_keys": ["ID"],
                        "left_amount": "Amount",
                        "right_amount": "Amount",
                        "left_unit_column": "Currency",
                        "right_unit_column": "Currency",
                        "entity": "Rollout entity",
                        "jurisdiction": "fixture-only",
                        "as_of": "2026-09-24",
                        "policy_revision": 1,
                    },
                )
            ]
        elif scenario == "archive_handoff":
            script = [
                call("read_document_page", {"file_id": fixture.file_id, "page": 1})
            ]
        elif scenario == "foreign_source":
            script = [call("search_resources", {"intent": fixture.workbooks[2]})]
        else:
            script = [call("inspect_resource", {"table_id": target})]
            if scenario != "approval_resume":
                script.append(
                    call(
                        "calculate",
                        {
                            "table_id": target,
                            "metrics": [{"column": "Amount", "operation": "sum"}],
                        },
                    )
                )
            script.append(
                call(
                    "prepare_table_append",
                    {
                        "table_id": target,
                        "records": [{"ID": "c-003", "Amount": 25, "Currency": "USD"}],
                    },
                )
            )
        script.append(AIMessage(content=self.answer))
        super().__init__(script)

    async def ainvoke(self, messages, config=None):
        """Execute the returned proposal identity without manufacturing a receipt.

        Args:
            messages: Current model context with real tool evidence.
            config: Existing tracing settings.

        Returns:
            Next scripted intent or execution of the prepared operation.
        """
        previous = messages[-1]
        if previous.type == "tool" and previous.name == "prepare_table_append":
            return call(
                "execute_operation",
                {"operation_ref": json.loads(previous.content)["operation_ref"]},
                "execute",
            )
        return await super().ainvoke(messages, config)


@pytest.mark.parametrize(
    "scenario,skip_calculation",
    [(scenario, False) for scenario in SCENARIOS] + [("multi_intent", True)],
)
async def test_rollout_runner_checks_real_state_and_replay(
    approval_client,  # noqa: F811
    scenario,
    skip_calculation,
):
    """Validate each runner path before any paid model trial."""
    existing = approval_client
    container = existing.container
    container.ledger_store = existing.fixture.store
    container.memory_documents = MemoryDocumentStore(container.db_client.pool)
    await container.memory_documents.initialize()
    container.tasks._require_approval = scenario == "approval_resume"
    async with comparison_owner(container.ledger_store.pool) as foreign:
        async with rollout_fixture(container, (existing.user_id, foreign)) as fixture:
            model = RolloutModel(scenario, fixture)
            if skip_calculation:
                model.responses = iter(
                    [
                        message
                        for message in model.responses
                        if not any(
                            tool["name"] == "calculate" for tool in message.tool_calls
                        )
                    ]
                )
            container.main_chat = MainChatService(
                model,
                container.main_chat._catalogue,
                container.db_client,
                operations=OperationService(container.ledger_store),
                tasks=container.tasks,
                memory_documents=container.memory_documents,
            )
            existing.client.headers.update(existing.headers)
            observed = {"scenario": scenario}
            if skip_calculation:
                with pytest.raises(
                    AssertionError, match="Missing pre-write calculation"
                ):
                    await run_rollout_trial(existing.client, fixture, observed)
                return
            await run_rollout_trial(existing.client, fixture, observed)
            assert observed["status"] == "state_passed_prose_pending"
            assert all(not turn["usage"]["complete"] for turn in observed["turns"])
