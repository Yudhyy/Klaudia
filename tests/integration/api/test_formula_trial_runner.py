"""Exercise the live trial runner offline against real HTTP and PostgreSQL."""

import json

import pytest
from langchain_core.messages import AIMessage

from tests.e2e.formula_cases import formula_prompt
from tests.e2e.test_formula_chat_e2e import (
    FormulaTrial,
    retain_final_state,
    run_formula_trial,
)
from tests.integration.api.test_durable_tasks import durable_client, approval_client  # noqa: F401
from tests.integration.api.test_main_chat_routes import main_chat_client  # noqa: F401
from tests.integration.api.test_typed_formula_chat import TypedChatModel, install_model
from tests.unit.test_main_agent import ScriptedModel, call


class TrialModel(TypedChatModel):
    """Follow fixed requests while preserving actual tool snapshot references."""

    async def ainvoke(self, messages, config=None):
        """Select a single operation from the current stored user request.

        Args:
            messages: Actual model context.
            config: Optional runtime callbacks.

        Returns:
            Scripted tool call or completion.
        """
        if messages[-1].type != "tool":
            request = json.loads(messages[-1].content)["current_request"]
            stage = next(
                stage
                for stage in (
                    "inputs",
                    "created",
                    "edited",
                    "failed",
                    "repaired",
                    "literals",
                )
                if formula_prompt(stage) == request
            )
            self.stage = {
                "inputs": 0,
                "created": 1,
                "edited": 2,
                "failed": 1,
                "repaired": 1,
                "literals": 0,
            }[stage]
            self.current_stage = stage
            self.operations = self.stage + 1
            self.formula_operation = (
                "divide" if stage in ("failed", "repaired") else "add"
            )
            self.formula_literal = {"failed": "0", "repaired": "2"}.get(stage, "0.2")
            return call("inspect_typed_workbook", {"workbook_id": self.workspace})
        if (
            messages[-1].name == "inspect_typed_workbook"
            and self.current_stage == "literals"
        ):
            snapshot = json.loads(messages[-1].content)
            values = [
                ("decimal", "+001.2300"),
                ("text", "00123"),
                ("boolean", True),
                ("date", "2026-09-21"),
                ("decimal", "9007199254740993"),
                ("decimal", "0.123456789012345678901234567890"),
            ]
            return call(
                "prepare_typed_edit",
                {
                    "workbook_id": self.workspace,
                    "snapshot": snapshot["snapshot"],
                    "edits": [
                        {
                            "action": "set_input",
                            "sheet_id": self.sheets[0],
                            "row": 2,
                            "column": column,
                            "input": {"kind": kind, "value": value},
                        }
                        for column, (kind, value) in enumerate(values, 1)
                    ],
                },
            )
        return await super().ainvoke(messages, config)


@pytest.mark.parametrize(
    "scenario", ["discovery", "recalculation", "failure_repair", "approval_resume"]
)
async def test_formula_trial_runner_with_scripted_model(approval_client, scenario):  # noqa: F811
    """The same runner grades real commits and two replay attempts per turn.

    Args:
        approval_client: Authenticated real database and task fixture.
        scenario: Workflow under test without paid model calls.
    """
    fixture = approval_client
    await install_model(fixture)
    sheets = fixture.container.main_chat._model.sheets
    fixture.container.main_chat._model = TrialModel(fixture.active_id, sheets)
    fixture.container.tasks._require_approval = scenario == "approval_resume"
    fixture.client.headers.update(fixture.headers)
    observed = {}
    await run_formula_trial(
        fixture.client,
        FormulaTrial(
            fixture.fixture.store,
            fixture.user_id,
            fixture.active_id,
            fixture.fixture.workbook_id,
            sheets,
            scenario,
        ),
        observed,
    )
    assert observed["prose_review"] == "pending human or separate semantic review"


async def test_failed_trial_retains_both_workbooks(durable_client):  # noqa: F811
    """A model that omits the edit fails grading without losing stored evidence.

    Args:
        durable_client: Authenticated isolated task and ledger fixture.
    """
    fixture = durable_client
    await install_model(fixture)
    sheets = fixture.container.main_chat._model.sheets
    fixture.container.main_chat._model = ScriptedModel(
        [AIMessage(content="No changes.")]
    )
    fixture.client.headers.update(fixture.headers)
    trial = FormulaTrial(
        fixture.fixture.store,
        fixture.user_id,
        fixture.active_id,
        fixture.fixture.workbook_id,
        sheets,
        "recalculation",
    )
    observed = {}
    with pytest.raises(AssertionError):
        try:
            await run_formula_trial(fixture.client, trial, observed)
        finally:
            await retain_final_state(trial, observed)
    assert set(observed["final_state"]) == {"target", "distractor"}
    assert observed["final_state"]["target"]["cells"] == []
    assert observed["responses"][0]["status_code"] == 200
