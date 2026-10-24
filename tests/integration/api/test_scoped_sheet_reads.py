"""Keep authenticated sheet HTTP reads working through scoped stdio MCP."""

from dataclasses import dataclass
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
import httpx
import pytest

from app.helpers import auth
from app.routes.v1.sheets import router
from app.services.auth.tokens import create_access_token
from app.services.core.container import _build_ledger_registry
from app.services.core.spreadsheets import SpreadsheetService
from config.settings import Settings
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL

HTTP_SECRET = "sheet-http-fixture-key-with-at-least-32-bytes"


@dataclass(frozen=True)
class SheetClient:
    """Hold an HTTP client and the current and foreign workbook IDs."""

    client: httpx.AsyncClient
    workbook: str
    foreign: str


@pytest.fixture
async def sheet_client(monkeypatch):
    """Connect the real sheet route to an owner-scoped MCP subprocess."""
    settings = Settings(
        _env_file=None,
        DATABASE_URL=POSTGRES_TEST_URL,
        JWT_SECRET=HTTP_SECRET,
        MCP_TRANSPORT="stdio",
    )

    def current_settings():
        """Use fixture credentials in the normal bearer dependency."""
        return settings

    monkeypatch.setattr(auth, "get_settings", current_settings)
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    owned = await store.create_spreadsheet(920001, f"sheet-api-{uuid.uuid4().hex}")
    foreign = await store.create_spreadsheet(920002, f"sheet-api-{uuid.uuid4().hex}")
    workbook, other = owned["spreadsheetId"], foreign["spreadsheetId"]
    registry = _build_ledger_registry(settings)
    try:
        await store.create_sheet(workbook, "Claims", grid=[["Amount"], [10]])
        await store.create_sheet(other, "Claims", grid=[["Amount"], [99]])
        await registry.connect()
        app = FastAPI()
        app.include_router(router, prefix="/v1")
        app.state.container = SimpleNamespace(
            mcp_ledger=registry, spreadsheets=SpreadsheetService(store)
        )
        headers = {
            "Authorization": "Bearer " + create_access_token(920001, HTTP_SECRET)
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
        ) as client:
            yield SheetClient(client, workbook, other)
    finally:
        await registry.disconnect()
        await store.delete_spreadsheet(workbook)
        await store.delete_spreadsheet(other)
        await store.close()


async def test_authenticated_sheet_grid_uses_forwarded_identity(sheet_client):
    """The protected route supplies authority outside model-visible arguments."""
    response = await sheet_client.client.get(
        "/v1/sheets/data",
        params={"spreadsheet_id": sheet_client.workbook, "sheet": "Claims"},
    )
    assert response.status_code == 200 and response.json()["values"] == [
        ["Amount"],
        [10],
    ]


async def test_authenticated_sheet_info_keeps_its_response(sheet_client):
    """The metadata route retains its existing response under MCP authorization."""
    response = await sheet_client.client.get(
        "/v1/sheets/info", params={"spreadsheet_id": sheet_client.workbook}
    )
    assert (
        response.status_code == 200
        and response.json()["sheets"][0]["title"] == "Claims"
    )


async def test_sheet_route_rejects_foreign_workbook(sheet_client):
    """A signed user cannot select another owner's workbook through HTTP."""
    response = await sheet_client.client.get(
        "/v1/sheets/data",
        params={"spreadsheet_id": sheet_client.foreign, "sheet": "Claims"},
    )
    assert response.status_code == 404
