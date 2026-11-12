"""Runtime adapters preserve scope and distinguish unsupported cases."""

from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

from klaudia.core.agent.agent import MainAgent
from tests.e2e.schema import Case, Turn, Expect
from tests.unit.test_main_agent import ScriptedModel, call


async def test_main_adapter_records_submitted_append_values_without_repairing_them():
    """Reports distinguish the user's request from a shortened model proposal."""
    from klaudia_api.interfaces.http.models.chat import KlaudiaMessage
    from tests.e2e.sut import MainAgentSUT, TurnRequest
    from tests.unit.test_agent_discovery_tools import descriptor
    from klaudia.core.skills.registry import SkillRegistry

    service = AsyncMock()
    service.inspect.return_value = descriptor()
    operations = AsyncMock()
    operations.prepare.return_value = {
        "operation_ref": "prepared:one",
        "status": "prepared",
    }
    submitted = {
        "table_id": "tbl_claims",
        "records": [{"Merchant": "Taxi", "Amount": 185000}],
    }
    model = ScriptedModel(
        [
            call("load_skill", {"name": "table-append"}, "procedure"),
            call("inspect_resource", {"table_id": "tbl_claims"}),
            call("prepare_table_append", submitted, "prepare"),
            AIMessage(content="Prepared"),
        ]
    )
    adapter = MainAgentSUT(MainAgent(model, service, operations=operations))
    view = await adapter.run(
        TurnRequest(
            [
                KlaudiaMessage(
                    role="user", content="Append Merchant Taxi vendor, Amount 185000"
                )
            ],
            42,
        )
    )
    assert view.append_attempts == [
        {"arguments": submitted, "operation_ref": "prepared:one"}
    ]
    assert view.operation_receipts == []
    assert view.loaded_skills == {
        "table-append": SkillRegistry().load("table-append")["version"]
    }
    assert view.append_attempts_observable
    assert operations.prepare.await_args.args[1].records == submitted["records"]


def test_append_observer_bounds_input_and_exposes_omissions():
    """Oversized diagnostic input cannot grow reports without a visible omission."""
    from uuid import uuid4
    from tests.e2e.sut import _FinancialToolObserver

    observer = _FinancialToolObserver()
    observer.on_tool_start(
        {"name": "prepare_table_append"},
        "",
        run_id=uuid4(),
        inputs={"records": [{"Merchant": "x" * 70000}]},
    )
    assert observer.append_attempts == []
    assert observer.append_attempts_omitted == 1


def test_append_observer_counts_missing_invalid_and_excess_attempts():
    """Diagnostic limits and unencodable input remain visible without tool failure."""
    from uuid import uuid4
    from tests.e2e.sut import MAX_APPEND_ATTEMPTS, _FinancialToolObserver

    observer = _FinancialToolObserver()
    for arguments in (None, {"Amount": float("nan")}):
        observer.on_tool_start(
            {"name": "prepare_table_append"}, "", run_id=uuid4(), inputs=arguments
        )
    for _ in range(MAX_APPEND_ATTEMPTS + 1):
        run_id = uuid4()
        observer.on_tool_start(
            {"name": "prepare_table_append"}, "", run_id=run_id, inputs={"records": []}
        )
        observer.on_tool_end({"operation_ref": "x" * 129}, run_id=run_id)
    assert len(observer.append_attempts) == MAX_APPEND_ATTEMPTS
    assert observer.append_attempts_omitted == 3
    assert all(attempt["operation_ref"] is None for attempt in observer.append_attempts)


def test_append_observation_is_a_snapshot_and_tool_errors_do_not_certify_preparation():
    """Failed tool calls retain their original inputs without a stored reference."""
    from uuid import uuid4
    from tests.e2e.sut import _FinancialToolObserver
    from tests.e2e.report import Report, TurnRecord
    from tests.e2e.checks import ResponseView, evaluate

    observer = _FinancialToolObserver()
    run_id = uuid4()
    arguments = {"records": [{"Merchant": "Taxi vendor"}]}
    observer.on_tool_start(
        {"name": "prepare_table_append"}, "", run_id=run_id, inputs=arguments
    )
    arguments["records"][0]["Merchant"] = "Changed"
    observer.on_tool_error(ValueError("invalid records"), run_id=run_id)
    assert observer.append_attempts == [
        {"arguments": {"records": [{"Merchant": "Taxi vendor"}]}, "operation_ref": None}
    ]
    view = ResponseView("", [], 1, append_attempts=observer.append_attempts)
    report = Report(
        [
            TurnRecord(
                "append", "write", "Append", 0, "Append", view, evaluate(Expect(), view)
            )
        ]
    )
    assert report.to_json()["turns"][0]["append_attempts"] == observer.append_attempts


async def test_main_adapter_observes_calculation_evidence():
    """The adapter captures tool results without exposing expected answers to the model."""
    from tests.e2e.sut import MainAgentSUT, TurnRequest
    from tests.unit.test_agent_discovery_tools import descriptor
    from klaudia_api.interfaces.http.models.chat import KlaudiaMessage

    service = AsyncMock()
    service.inspect.return_value = descriptor()
    evidence = {
        "source": {"table_id": "tbl_claims"},
        "groups": [
            {
                "key": {},
                "metrics": [{"column": "Amount", "operation": "sum", "value": "20"}],
            }
        ],
    }
    service.calculate.return_value = evidence
    model = ScriptedModel(
        [
            call("inspect_resource", {"table_id": "tbl_claims"}),
            call(
                "calculate",
                {
                    "table_id": "tbl_claims",
                    "metrics": [{"column": "Amount", "operation": "sum"}],
                },
                "sum",
            ),
            AIMessage(content="Amount: 20"),
        ]
    )
    sut = MainAgentSUT(MainAgent(model, service))
    view = await sut.run(
        TurnRequest(
            messages=[KlaudiaMessage(role="user", content="Sum claims")],
            user_id=42,
            spreadsheet_id="active",
        )
    )
    assert view.calculations == [evidence]
    assert view.capabilities_attempted == ["calculate", "inspect_resource"]
    assert view.runtime == "main"
    assert service.calculate.await_args.args[0] == 42


def test_main_adapter_rejects_legacy_scope_and_session_contracts():
    """Historical bound-workbook tests cannot silently become owned-workbook tests."""
    from tests.e2e.sut import MainAgentSUT

    sut = MainAgentSUT(AsyncMock())
    case = Case(
        id="scope", category="isolation", title="Bound", turns=[Turn(user="Read")]
    )
    assert "bound_workbook" in sut.unsupported(case)
    case.resource_scope = "owned_workbooks"
    assert sut.unsupported(case) is None
    case.turns.append(Turn(user="Continue"))
    assert "multi-turn" in sut.unsupported(case)


def test_main_adapter_does_not_rewrite_worker_expectations():
    """Authoring new capability checks must be explicit, never a tool-name substitution."""
    from tests.e2e.sut import MainAgentSUT

    case = Case(
        id="legacy",
        category="routing",
        title="Worker route",
        resource_scope="owned_workbooks",
        turns=[Turn(user="Read", expect=Expect(route="data_entry_team"))],
    )
    assert "legacy" in MainAgentSUT(AsyncMock()).unsupported(case)


def test_main_adapter_rejects_unobservable_cache_requirements():
    """Unavailable cache observations cannot silently pass through skip logic."""
    from tests.e2e.sut import MainAgentSUT

    case = Case(
        id="cache",
        category="cache",
        title="Cache hit",
        resource_scope="owned_workbooks",
        turns=[Turn(user="Read", expect=Expect(cache_hits=1))],
    )
    assert "cache" in MainAgentSUT(AsyncMock()).unsupported(case)


async def test_outer_timeout_retains_committed_operation_evidence(monkeypatch):
    """Runner cancellation after commit preserves receipts for state inspection."""
    import asyncio
    from tests.e2e import engine_inprocess
    from tests.e2e.sut import MainAgentSUT

    class WaitingModel(ScriptedModel):
        """Wait for the runner timeout after one successful tool call."""

        async def ainvoke(self, messages, config=None):
            """Execute once, then wait without emitting a final answer."""
            if self.inputs:
                await asyncio.Event().wait()
            return await super().ainvoke(messages, config)

    operations = AsyncMock()
    receipt = {
        "operation_id": "op_one",
        "status": "committed",
        "target": {"sheet_id": 8},
    }
    operations.execute.return_value = receipt
    model = WaitingModel([call("execute_operation", {"operation_ref": "prepared:old"})])
    monkeypatch.setattr(engine_inprocess, "TURN_TIMEOUT_S", 0.05)
    case = Case(
        id="timeout",
        category="write",
        title="Lost answer",
        resource_scope="owned_workbooks",
        turns=[Turn(user="Execute")],
    )
    records = await engine_inprocess.run_case_inprocess(
        None,
        None,
        None,
        case,
        sut=MainAgentSUT(MainAgent(model, AsyncMock(), operations=operations)),
    )
    assert not records[0].result.passed
    assert records[0].view.operation_receipts == [receipt]
    assert records[0].view.operation_references == ["prepared:old"]
    assert not records[0].view.append_attempts_observable


async def test_state_observation_failure_retains_receipts_and_report_row():
    """A failed post-run database read must not lose the committed operation record."""
    from tests.e2e.engine_inprocess import run_case_inprocess
    from tests.e2e.sut import MainAgentSUT

    operations = AsyncMock()
    receipt = {
        "operation_id": "op_one",
        "status": "committed",
        "target": {"sheet_id": 8},
    }
    operations.execute.return_value = receipt
    model = ScriptedModel(
        [
            call("execute_operation", {"operation_ref": "prepared:old"}),
            AIMessage(content="Committed"),
        ]
    )
    case = Case(
        id="state-failure",
        category="write",
        title="State unavailable",
        resource_scope="owned_workbooks",
        turns=[Turn(user="Execute", expect=Expect(ledger_state={"Claims": [[20]]}))],
    )
    observer = AsyncMock(side_effect=ConnectionError("snapshot unavailable"))
    records = await run_case_inprocess(
        None,
        None,
        None,
        case,
        sut=MainAgentSUT(MainAgent(model, AsyncMock(), operations=operations)),
        observe_state=observer,
    )
    assert len(records) == 1
    assert not records[0].result.passed
    assert "state observation" in records[0].view.error
    assert records[0].view.operation_receipts == [receipt]
