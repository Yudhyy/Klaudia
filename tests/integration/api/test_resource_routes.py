"""Authenticated discovery across owned workbooks through the real API boundary."""

from types import SimpleNamespace
import uuid

import httpx
import pytest
from fastapi import FastAPI

from klaudia_api.interfaces.http.v1.resources import router
from klaudia_api.adapters.ledger.catalogue.service import CatalogueService
from app.services.auth.tokens import create_access_token
from klaudia_api.composition.settings import get_settings
from ledger.catalogue import CatalogueStore
from ledger.resources import TableRegistration
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL


@pytest.fixture
async def resource_client():
    """Build the real ownership store and signed-token API with isolated records."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    catalogue = CatalogueStore(store.pool)
    workbooks = []
    tables = []
    try:
        for owner in (90301, 90301, 90302):
            workbook = await store.create_spreadsheet(owner, f"api-{uuid.uuid4().hex}")
            workbooks.append(workbook["spreadsheetId"])
            sheet = await store.create_sheet(
                workbooks[-1], "Claims", [["Employee", "Amount"]]
            )
            table = await catalogue.register(
                workbooks[-1],
                TableRegistration(
                    sheet_id=sheet["sheetId"],
                    expected_sheet_revision=0,
                    table_range="A1:B1",
                    name="Employee claims",
                ),
            )
            tables.append(table["table_id"])
        app = FastAPI()
        app.state.container = SimpleNamespace(catalogue=CatalogueService(catalogue))
        app.include_router(router, prefix="/v1")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, tables, app
    finally:
        for workbook in workbooks:
            await store.delete_spreadsheet(workbook)
        await store.close()


def auth_headers(user_id: int = 90301) -> dict[str, str]:
    """Sign a real access token for the isolated test owner."""
    token = create_access_token(user_id, secret=get_settings().jwt_secret)
    return {"Authorization": f"Bearer {token}"}


async def test_resource_routes_require_authentication(resource_client):
    """Both discovery entry points reject callers without a signed identity."""
    client, tables, _ = resource_client
    assert (
        await client.post("/v1/resources/search", json={"intent": "claims"})
    ).status_code == 401
    assert (await client.get(f"/v1/resources/{tables[0]}")).status_code == 401


async def test_search_identity_comes_from_token(resource_client):
    """Search spans owned workbooks and rejects model-supplied scope fields."""
    client, tables, _ = resource_client
    response = await client.post(
        "/v1/resources/search",
        json={"intent": "claims", "limit": 2},
        headers=auth_headers(),
    )
    assert response.status_code == 200, response.text
    assert {
        candidate["table_id"] for candidate in response.json()["candidates"]
    } == set(tables[:2])
    assert response.json()["has_more"] is False
    for field in ("user_id", "spreadsheet_id", "authorised_scope"):
        rejected = await client.post(
            "/v1/resources/search",
            json={"intent": "claims", field: 90302},
            headers=auth_headers(),
        )
        assert rejected.status_code == 422


async def test_inspection_hides_foreign_identity_and_paginates(resource_client):
    """Foreign and missing IDs have the same response; visible schemas paginate."""
    client, tables, _ = resource_client
    foreign = await client.get(f"/v1/resources/{tables[2]}", headers=auth_headers())
    missing = await client.get("/v1/resources/tbl_missing", headers=auth_headers())
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    page = await client.get(
        f"/v1/resources/{tables[0]}?column_limit=1&column_offset=1",
        headers=auth_headers(),
    )
    assert page.status_code == 200
    assert page.json()["columns"][0]["name"] == "Amount"
    assert page.json()["has_more_columns"] is False
    invalid = await client.get(
        f"/v1/resources/{tables[0]}?column_limit=257", headers=auth_headers()
    )
    assert invalid.status_code == 422


async def test_catalogue_unavailable_returns_service_error(resource_client):
    """Unsupported backends fail without broadening discovery scope."""
    client, _, app = resource_client
    app.state.container.catalogue = None
    response = await client.post(
        "/v1/resources/search", json={"intent": "claims"}, headers=auth_headers()
    )
    assert response.status_code == 503
