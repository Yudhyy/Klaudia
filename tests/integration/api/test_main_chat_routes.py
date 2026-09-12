"""Authenticated main chat uses real catalogue, ledger and conversation storage."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import AIMessage

from app.routes.v1.chat import router as chat_router
from app.routes.v1.sessions import router as session_router
from app.services.auth.tokens import create_access_token
from app.services.catalogue.service import CatalogueService
from app.services.core.main_chat import MainChatService
from app.services.core.operations import OperationService
from app.services.core.orchestrator import KlaudiaOrchestrator
from app.services.core.spreadsheets import SpreadsheetService
from app.services.extraction.infra.db_client import AppDBClient
from config.settings import Settings, get_settings
from ledger.catalogue import CatalogueStore
from ledger.store import LedgerStore
from tests.e2e.capability_cases import seeded_capability_case
from tests.integration.postgres import POSTGRES_TEST_URL
from tests.unit.test_main_agent import ScriptedModel, call


class AppendChatModel(ScriptedModel):
    """Discover the real table and execute only the returned checked reference."""

    def __init__(self):
        """Start a discovery request without embedding workbook coordinates."""
        super().__init__([call("search_resources", {"intent": "Claims"})])

    async def ainvoke(self, messages, config=None):
        """Advance from actual tool evidence to a complete named append.

        Args:
            messages: Current bounded model context.
            config: Application tracing configuration.

        Returns:
            A deterministic tool request or receipt-grounded answer.
        """
        previous = messages[-1]
        if previous.type != "tool":
            return await super().ainvoke(messages, config)
        self.inputs.append(list(messages))
        evidence = json.loads(previous.content)
        if previous.name == "search_resources":
            return call(
                "inspect_resource",
                {"table_id": evidence["candidates"][0]["table_id"]},
                "inspect",
            )
        if previous.name == "inspect_resource":
            return call(
                "prepare_table_append",
                {
                    "table_id": evidence["table_id"],
                    "records": [
                        {
                            "Date": "2026-06-30",
                            "Merchant": "Taxi vendor",
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
        return AIMessage(
            content="Added the expense. Formula calculation is unsupported."
        )


@pytest.fixture
async def main_chat_client():
    """Create fresh identities and workbooks with cleanup limited to this fixture."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=POSTGRES_TEST_URL,
        CHAT_RUNTIME="main",
        NUMERIC_VERIFY_MODE="off",
    )
    database = AppDBClient(settings)
    store = LedgerStore(POSTGRES_TEST_URL)
    await database.connect()
    await store.connect()
    identity = uuid4().hex
    user_id = await database.fetchval(
        'INSERT INTO "user" (username, email, password_hash) VALUES ($1, $2, $3) RETURNING user_id',
        (identity, f"{identity}@invalid.example", "!disabled-test-account"),
    )
    active = await store.create_spreadsheet(user_id, "Active workbook")
    try:
        async with seeded_capability_case(store, "append", user_id=user_id) as fixture:
            model = AppendChatModel()
            guardrails = AsyncMock()
            guardrails.validate_input.return_value = SimpleNamespace(passed=True)
            guardrails.validate_output.return_value = SimpleNamespace(passed=True)
            catalogue = CatalogueService(CatalogueStore(store.pool))
            container = SimpleNamespace(
                settings=settings,
                db_client=database,
                guardrails=guardrails,
                main_chat=MainChatService(
                    model, catalogue, database, operations=OperationService(store)
                ),
                spreadsheets=SpreadsheetService(store),
                supervisor=None,
                extraction_agent=None,
                memory=None,
                langfuse=None,
            )
            app = FastAPI()
            app.state.container = container
            app.state.orchestrator = KlaudiaOrchestrator(container)
            app.include_router(chat_router, prefix="/v1")
            app.include_router(session_router, prefix="/v1")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield SimpleNamespace(
                    client=client,
                    container=container,
                    model=model,
                    fixture=fixture,
                    user_id=user_id,
                    active_id=active["spreadsheetId"],
                    headers={
                        "Authorization": "Bearer "
                        + create_access_token(user_id, secret=get_settings().jwt_secret)
                    },
                )
    finally:
        await store.delete_spreadsheet(active["spreadsheetId"])
        await database.execute(
            "DELETE FROM pages WHERE metadata_file_id IN (SELECT id FROM metadata_file WHERE user_id = $1)",
            (user_id,),
        )
        await database.execute(
            "DELETE FROM metadata_file WHERE user_id = $1", (user_id,)
        )
        await database.execute(
            "DELETE FROM conversation WHERE user_id = $1", (user_id,)
        )
        await database.execute("DELETE FROM session WHERE user_id = $1", (user_id,))
        await database.execute('DELETE FROM "user" WHERE user_id = $1', (user_id,))
        await store.close()
        await database.close()


@pytest.mark.parametrize("streaming", [False, True])
async def test_main_chat_appends_outside_active_workbook_and_replays_once(
    main_chat_client, streaming
):
    """JWT identity selects owned resources; replay preserves complete workbook state."""
    fixture = main_chat_client
    request = {
        "messages": [{"role": "user", "content": fixture.fixture.case.turns[0].user}],
        "spreadsheet_id": fixture.active_id,
    }
    route = "/v1/chat/stream" if streaming else "/v1/chat"
    response = await fixture.client.post(route, json=request, headers=fixture.headers)
    assert response.status_code == 200, response.text
    if streaming:
        payload = next(
            json.loads(frame.split("data: ", 1)[1])
            for frame in response.text.split("\n\n")
            if frame.startswith("event: done")
        )
    else:
        payload = response.json()
    assert payload["runtime"] == "main"
    assert payload["run_status"] == "answered"
    assert len(payload["operation_receipts"]) == 1
    receipt = payload["operation_receipts"][0]
    assert receipt["target"]["spreadsheet_id"] == fixture.fixture.workbook_id
    expected = fixture.fixture.case.turns[0].expect.ledger_state
    assert await fixture.fixture.observe_state() == expected
    reference = payload["operation_references"][0]
    saved = await fixture.client.get(
        f"/v1/sessions/{payload['session_id']}", headers=fixture.headers
    )
    assert reference in saved.text
    replay_model = ScriptedModel(
        [
            call("execute_operation", {"operation_ref": reference}),
            AIMessage(content="Original append confirmed."),
        ]
    )
    fixture.container.main_chat._model = replay_model
    replay = await fixture.client.post(
        "/v1/chat",
        headers=fixture.headers,
        json={
            **request,
            "session_id": payload["session_id"],
            "messages": [{"role": "user", "content": f"Recover operation {reference}"}],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_receipts"] == [receipt]
    assert await fixture.fixture.observe_state() == expected


async def test_foreign_session_and_active_workbook_never_reach_main_model(
    main_chat_client,
):
    """Existing chat boundary rejects foreign identity before model execution."""
    fixture = main_chat_client
    request = {"messages": [{"role": "user", "content": "Read Claims"}]}
    assert (await fixture.client.post("/v1/chat", json=request)).status_code == 401
    for selection in (
        {"session_id": 2147483647},
        {"spreadsheet_id": "missing-workbook"},
    ):
        for route in ("/v1/chat", "/v1/chat/stream"):
            response = await fixture.client.post(
                route, json={**request, **selection}, headers=fixture.headers
            )
            assert response.status_code == 404
    assert fixture.model.inputs == []


async def test_archived_document_is_retrievable_outside_history_with_owner_checks(
    main_chat_client,
):
    """Bounded archive tools retrieve older pages without leaking foreign metadata."""
    from app.services.core.archive_tools import ArchiveTools
    from ledger.resources import ResourceNotFoundError

    fixture = main_chat_client
    database = fixture.container.db_client
    session_id = await database.create_session(fixture.user_id)
    file_id = await database.fetchval(
        "INSERT INTO metadata_file (session_id, user_id, type, total_pages, file_name, status) VALUES ($1, $2, 'pdf', 1, 'historic-invoice.pdf', 'completed') RETURNING id",
        (session_id, fixture.user_id),
    )
    extracted = "invoice detail " * 1000
    await database.execute(
        "INSERT INTO pages (metadata_file_id, page, agent_extracted, status) VALUES ($1, 1, $2, 'extracted')",
        (file_id, extracted),
    )
    for index in range(12):
        await database.save_message(
            session_id, fixture.user_id, "user", f"Later message {index}"
        )
    model = ScriptedModel(
        [
            call("search_documents", {"name": "historic-invoice"}),
            call("read_document_page", {"file_id": file_id, "page": 1}, "page"),
            call(
                "read_document_page",
                {"file_id": file_id, "page": 1, "offset": 8192},
                "rest",
            ),
            AIMessage(content="Read the archived invoice."),
        ]
    )
    fixture.container.main_chat._model = model
    response = await fixture.client.post(
        "/v1/chat",
        headers=fixture.headers,
        json={
            "session_id": session_id,
            "spreadsheet_id": fixture.active_id,
            "messages": [{"role": "user", "content": "Read historic-invoice.pdf"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["run_status"] == "answered"
    evidence = [
        json.loads(message.content)
        for message in model.inputs[-1]
        if message.type == "tool"
    ]
    assert evidence[0]["documents"][0]["file_id"] == file_id
    assert evidence[1]["next_offset"] == 8192
    assert evidence[2]["next_offset"] is None
    assert evidence[1]["extraction_text"] + evidence[2]["extraction_text"] == extracted
    foreign = ArchiveTools(database, fixture.user_id + 1000000)
    assert await foreign.search("historic-invoice") == {
        "documents": [],
        "next_offset": None,
    }
    with pytest.raises(ResourceNotFoundError):
        await foreign.read(file_id, 1)
    with pytest.raises(ResourceNotFoundError):
        await foreign.read(2147483647, 1)
