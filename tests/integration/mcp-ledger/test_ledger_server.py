"""Exercise the scoped read-only ledger surface over real MCP transports."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import socket
import sys
import uuid

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
import httpx
import jwt
import pytest

from ledger.catalogue import CatalogueStore
from ledger.resources import TableRegistration
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL
from app.adapters.mcp_client.tool_registry import MCPToolRegistry

SERVER_DIR = Path(__file__).resolve().parents[3] / "mcp-ledger"
TRANSPORT_SECRET = "transport-fixture-secret-with-at-least-32-bytes"
APPLICATION_SECRET = "application-fixture-secret-with-at-least-32-bytes"
READ_TOOLS = {
    "tool_get_sheet_data",
    "tool_get_sheet_formulas",
    "tool_list_sheets",
    "tool_get_spreadsheet_info",
    "tool_get_multiple_sheet_data",
    "tool_get_sheet_snapshot",
    "tool_aggregate_sheet",
    "tool_search_resources",
    "tool_inspect_resource",
}


@dataclass(frozen=True)
class OwnedLedger:
    """Retain two owners and their isolated workbook fixtures."""

    store: LedgerStore
    owner: int
    other_owner: int
    workbook: str
    foreign: str
    foreign_table: str


def signed_token(subject: str, secret: str = TRANSPORT_SECRET, **claims) -> str:
    """Issue a fixture credential with optional claim overrides."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {"sub": subject, "exp": now + datetime.timedelta(minutes=5)}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
async def owned_ledger():
    """Seed unique test workbooks without exposing mutation tools to MCP."""
    store = LedgerStore(POSTGRES_TEST_URL)
    await store.connect()
    first = await store.create_spreadsheet(910001, f"mcp-owned-{uuid.uuid4().hex}")
    second = await store.create_spreadsheet(910002, f"mcp-foreign-{uuid.uuid4().hex}")
    workbook, foreign = first["spreadsheetId"], second["spreadsheetId"]
    try:
        await store.create_sheet(
            workbook, "Claims", grid=[["Name", "Amount"], ["A", 10]]
        )
        await store.create_sheet(
            foreign, "Claims", grid=[["Name", "Amount"], ["B", 99]]
        )
        snapshot = await store.get_snapshot(foreign, "Claims")
        table = await CatalogueStore(store.pool).register(
            foreign,
            TableRegistration(
                sheet_id=snapshot.sheet_id,
                expected_sheet_revision=snapshot.revision,
                table_range="A1:B2",
                name="Foreign claims",
            ),
        )
        yield OwnedLedger(store, 910001, 910002, workbook, foreign, table["table_id"])
    finally:
        await store.delete_spreadsheet(workbook)
        await store.delete_spreadsheet(foreign)
        await store.close()


def server_environment(fixture: OwnedLedger) -> dict[str, str]:
    """Bind server defaults to fixture stores and explicit credentials."""
    return {
        **os.environ,
        "DATABASE_URL": POSTGRES_TEST_URL,
        "LEDGER_WORKSPACE": fixture.workbook,
        "MCP_USER_ID": str(fixture.owner),
        "JWT_SECRET": APPLICATION_SECRET,
        "MCP_JWT_SECRET": TRANSPORT_SECRET,
        "MCP_JWT_ISSUER": "",
        "MCP_JWT_AUDIENCE": "",
    }


@asynccontextmanager
async def stdio_client(fixture: OwnedLedger, overrides: dict[str, str] | None = None):
    """Run the actual stdio server for the configured local owner."""
    transport = StdioTransport(
        command=sys.executable,
        args=["main.py", "--transport", "stdio"],
        cwd=str(SERVER_DIR),
        env={**server_environment(fixture), **(overrides or {})},
    )
    async with Client(transport) as client:
        yield client


async def call_json(client: Client, name: str, arguments: dict):
    """Parse the existing MCP JSON text response."""
    outcome = await client.call_tool(name, arguments)
    texts = [item.text for item in outcome.content if item.type == "text"]
    if not texts:
        return []
    return (
        json.loads(texts[0])
        if len(texts) == 1
        else [json.loads(text) for text in texts]
    )


async def test_stdio_advertises_only_read_tools(owned_ledger):
    """The default surface cannot advertise legacy writes."""
    async with stdio_client(owned_ledger) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == READ_TOOLS


async def test_stdio_reads_keep_shapes_and_fuzzy_titles(owned_ledger):
    """Safe read names, ranges, and response values stay compatible."""
    async with stdio_client(owned_ledger) as client:
        snapshot = await call_json(
            client, "tool_get_sheet_snapshot", {"sheet": "claims"}
        )
        assert snapshot["values"] == [["Name", "Amount"], ["A", 10]]
        ranged = await call_json(
            client, "tool_get_sheet_data", {"sheet": "CLAIMS", "range": "B2"}
        )
        assert ranged["values"] == [[10]]
        info = await call_json(client, "tool_get_spreadsheet_info", {})
        assert info["sheets"][0]["title"] == "Claims"
        aggregate = await call_json(
            client,
            "tool_aggregate_sheet",
            {
                "sheet": "Claims",
                "query": {
                    "table_range": "A1:B2",
                    "metrics": [{"column": "Amount", "operation": "sum"}],
                },
            },
        )
        assert aggregate["groups"][0]["metrics"][0]["value"] == "10"


@pytest.mark.parametrize(
    "name,extra",
    [
        ("tool_get_sheet_data", {"sheet": "Claims"}),
        ("tool_get_sheet_formulas", {"sheet": "Claims"}),
        ("tool_list_sheets", {}),
        ("tool_get_spreadsheet_info", {}),
        ("tool_get_sheet_snapshot", {"sheet": "Claims"}),
        (
            "tool_aggregate_sheet",
            {
                "sheet": "Claims",
                "query": {
                    "table_range": "A1:B2",
                    "metrics": [{"column": "Amount", "operation": "sum"}],
                },
            },
        ),
        ("tool_search_resources", {"query": {"intent": "claims"}}),
        ("tool_inspect_resource", {"table_id": "absent"}),
    ],
)
async def test_stdio_reads_cannot_select_foreign_workbooks(owned_ledger, name, extra):
    """Every single-workbook read checks current ownership."""
    async with stdio_client(owned_ledger) as client:
        outcome = await client.call_tool(
            name,
            {**extra, "spreadsheet_id": owned_ledger.foreign},
            raise_on_error=False,
        )
    assert outcome.is_error and "Workbook not found" in str(outcome.content)


async def test_batch_reads_reject_a_foreign_nested_workbook(owned_ledger):
    """An owned outer context cannot authorize foreign batch selections."""
    async with stdio_client(owned_ledger) as client:
        outcome = await client.call_tool(
            "tool_get_multiple_sheet_data",
            {
                "queries": [
                    {"sheet": "Claims"},
                    {"sheet": "Claims", "spreadsheet_id": owned_ledger.foreign},
                ],
            },
            raise_on_error=False,
        )
    assert outcome.is_error and "Workbook not found" in str(outcome.content)


async def test_foreign_table_cannot_hide_in_owned_workbook(owned_ledger):
    """A table ID from another workbook remains out of scope."""
    async with stdio_client(owned_ledger) as client:
        outcome = await client.call_tool(
            "tool_inspect_resource",
            {"table_id": owned_ledger.foreign_table},
            raise_on_error=False,
        )
    assert outcome.is_error


async def test_owned_catalogue_search_and_inspection_keep_their_contract(owned_ledger):
    """Scoped catalogue reads retain table identity and schema evidence."""
    snapshot = await owned_ledger.store.get_snapshot(owned_ledger.workbook, "Claims")
    table = await CatalogueStore(owned_ledger.store.pool).register(
        owned_ledger.workbook,
        TableRegistration(
            sheet_id=snapshot.sheet_id,
            expected_sheet_revision=snapshot.revision,
            table_range="A1:B2",
            name="Employee claims",
        ),
    )
    async with stdio_client(owned_ledger) as client:
        found = await call_json(
            client, "tool_search_resources", {"query": {"intent": "Employee claims"}}
        )
        assert found["candidates"][0]["table_id"] == table["table_id"]
        inspected = await call_json(
            client, "tool_inspect_resource", {"table_id": table["table_id"]}
        )
    assert inspected["freshness"] == "current" and len(inspected["columns"]) == 2


async def test_owned_batch_keeps_ranges_and_missing_sheet_errors(owned_ledger):
    """An authorized batch keeps its existing per-sheet result shape."""
    async with stdio_client(owned_ledger) as client:
        batch = await call_json(
            client,
            "tool_get_multiple_sheet_data",
            {"queries": [{"sheet": "Claims", "range": "B2"}, {"sheet": "Missing"}]},
        )
    assert batch[0]["data"] == [[10]] and "error" in batch[1]


async def test_snapshot_and_aggregate_budgets_remain_enforced(owned_ledger):
    """Ownership checks do not weaken existing range and output limits."""
    await owned_ledger.store.create_sheet(
        owned_ledger.workbook, "Large", grid=[["Category", "Amount"], ["x" * 65536, 1]]
    )
    async with stdio_client(owned_ledger) as client:
        unbounded = await client.call_tool(
            "tool_get_sheet_snapshot",
            {"sheet": "Claims", "range": "A:Z"},
            raise_on_error=False,
        )
        assert unbounded.is_error
        snapshot = await client.call_tool(
            "tool_get_sheet_snapshot", {"sheet": "Large"}, raise_on_error=False
        )
        assert snapshot.is_error
        aggregate = await client.call_tool(
            "tool_aggregate_sheet",
            {
                "sheet": "Large",
                "query": {
                    "table_range": "A1:B2",
                    "metrics": [{"column": "Amount", "operation": "sum"}],
                    "group_by": ["Category"],
                },
            },
            raise_on_error=False,
        )
        assert aggregate.is_error


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("tool_delete_sheet", {"sheet": "Claims"}),
        ("tool_batch_update", {"requests": [{"deleteSheet": {"sheetId": 1}}]}),
        (
            "tool_append_rows_checked",
            {
                "operation": {
                    "sheet_id": 1,
                    "expected_revision": 0,
                    "idempotency_key": "stale",
                    "rows": [[777]],
                }
            },
        ),
    ],
)
async def test_direct_mutations_leave_both_workbooks_unchanged(
    owned_ledger, name, arguments
):
    """Direct destructive and stale checked calls cannot change either owner."""
    before = [
        await owned_ledger.store.get_snapshot(workbook, "Claims")
        for workbook in (owned_ledger.workbook, owned_ledger.foreign)
    ]
    async with stdio_client(owned_ledger) as client:
        outcome = await client.call_tool(name, arguments, raise_on_error=False)
        assert outcome.is_error and "MCP tool is not available" in str(outcome.content)
    after = [
        await owned_ledger.store.get_snapshot(workbook, "Claims")
        for workbook in (owned_ledger.workbook, owned_ledger.foreign)
    ]
    assert after == before


async def test_ownership_revocation_applies_to_existing_stdio_session(owned_ledger):
    """A connected caller does not retain an earlier ownership grant."""
    async with stdio_client(owned_ledger) as client:
        await call_json(client, "tool_list_sheets", {})
        await owned_ledger.store.pool.execute(
            "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
            owned_ledger.other_owner,
            owned_ledger.workbook,
        )
        outcome = await client.call_tool("tool_list_sheets", {}, raise_on_error=False)
    assert outcome.is_error and "Workbook not found" in str(outcome.content)


async def test_parallel_reads_reuse_the_ownership_connection(owned_ledger, monkeypatch):
    """Five authorized reads finish without acquiring a second pool connection."""
    from ledger.server import mcp

    reached = 0
    ready = asyncio.Event()
    original = owned_ledger.store.list_sheets

    async def list_sheets(workspace):
        """Wait until every authorization holds one of the five pool slots."""
        nonlocal reached
        reached += 1
        if reached == 5:
            ready.set()
        await ready.wait()
        return await original(workspace)

    @asynccontextmanager
    async def lifespan(server):
        """Use the real fixture pool inside the in-process server."""
        yield owned_ledger.store

    monkeypatch.setattr(mcp, "_lifespan", lifespan)
    monkeypatch.setattr(owned_ledger.store, "list_sheets", list_sheets)
    monkeypatch.setenv("MCP_USER_ID", str(owned_ledger.owner))
    async with Client(mcp) as client:
        outputs = await asyncio.wait_for(
            asyncio.gather(
                *[
                    client.call_tool(
                        "tool_list_sheets", {"spreadsheet_id": owned_ledger.workbook}
                    )
                    for _ in range(5)
                ]
            ),
            timeout=5,
        )
    assert all(not output.is_error for output in outputs)


@asynccontextmanager
async def network_server(
    fixture: OwnedLedger, log_path: Path, overrides: dict[str, str] | None = None
):
    """Run Streamable HTTP with an intentionally set local owner."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **server_environment(fixture),
        "FASTMCP_HOST": "127.0.0.1",
        "FASTMCP_PORT": str(port),
        "FASTMCP_STREAMABLE_HTTP_PATH": "/mcp",
        "FASTMCP_JSON_RESPONSE": "false",
        "FASTMCP_HTTP_ALLOWED_HOSTS": '["127.0.0.1", "ledger.example"]',
        "FASTMCP_HTTP_ALLOWED_ORIGINS": '["https://klaudia.example"]',
        **(overrides or {}),
    }
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "main.py",
            "--transport",
            "http",
            cwd=SERVER_DIR,
            env=environment,
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if process.returncode is not None:
                    raise AssertionError("HTTP MCP server stopped before startup")
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.05)
                else:
                    writer.close()
                    await writer.wait_closed()
                    break
            else:
                raise AssertionError("HTTP MCP server did not start")
            yield f"http://127.0.0.1:{port}{environment['FASTMCP_STREAMABLE_HTTP_PATH']}"
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


@pytest.fixture(params=["false", "true"], ids=["sse-response", "json-response"])
def network_settings(request):
    """Exercise both response encodings on a custom Streamable HTTP path."""
    return {
        "FASTMCP_JSON_RESPONSE": request.param,
        "FASTMCP_STREAMABLE_HTTP_PATH": "/ledger/read",
    }


@pytest.fixture
async def http_endpoint(owned_ledger, tmp_path, network_settings):
    async with network_server(
        owned_ledger, tmp_path / "http.log", network_settings
    ) as url:
        yield url


async def test_network_tokens_bind_workbook_ownership(
    owned_ledger, tmp_path, network_settings
):
    """Verified HTTP subjects can read only their currently owned workbooks."""
    async with network_server(
        owned_ledger, tmp_path / "network.log", network_settings
    ) as url:
        async with Client(url, auth=signed_token(str(owned_ledger.owner))) as client:
            own = await client.call_tool("tool_list_sheets", {})
            assert not own.is_error
            denied = await client.call_tool(
                "tool_list_sheets",
                {"spreadsheet_id": owned_ledger.foreign},
                raise_on_error=False,
            )
            assert denied.is_error
        async with Client(
            url, auth=signed_token(str(owned_ledger.other_owner))
        ) as client:
            own = await client.call_tool(
                "tool_get_sheet_data",
                {"sheet": "Claims", "spreadsheet_id": owned_ledger.foreign},
            )
            assert not own.is_error


async def test_network_service_identity_cannot_use_local_owner(
    owned_ledger, tmp_path, network_settings
):
    """A process owner setting never authorizes an HTTP service credential."""
    async with network_server(
        owned_ledger, tmp_path / "network.log", network_settings
    ) as url:
        async with Client(url, auth=signed_token("service")) as client:
            outcome = await client.call_tool(
                "tool_list_sheets", {}, raise_on_error=False
            )
    assert outcome.is_error and "Owner identity required" in str(outcome.content)


@pytest.mark.parametrize(
    "metadata_token",
    [
        signed_token("910001", APPLICATION_SECRET, exp=0),
        signed_token("910001", "a-forged-application-key-with-32-bytes"),
        signed_token("service", APPLICATION_SECRET),
        jwt.encode({"sub": "910001"}, APPLICATION_SECRET, algorithm="HS256"),
    ],
    ids=["expired", "forged", "service-subject", "missing-expiry"],
)
async def test_invalid_delegation_does_not_fall_back_to_process_owner(
    owned_ledger, metadata_token
):
    """Expired, forged, nonnumeric, and unbounded tokens fail closed."""
    async with stdio_client(owned_ledger) as client:
        outcome = await client.call_tool(
            "tool_list_sheets",
            {},
            meta={"authorization": metadata_token},
            raise_on_error=False,
        )
    assert outcome.is_error and "Owner identity required" in str(outcome.content)


async def test_delegated_identities_do_not_leak_between_calls(owned_ledger):
    """A shared stdio session resolves each caller from its signed credential."""
    async with stdio_client(owned_ledger) as client:
        for owner, workbook in [
            (owned_ledger.owner, owned_ledger.workbook),
            (owned_ledger.other_owner, owned_ledger.foreign),
        ]:
            token = signed_token(str(owner), APPLICATION_SECRET)
            outcome = await client.call_tool(
                "tool_list_sheets",
                {"spreadsheet_id": workbook},
                meta={"authorization": token},
            )
            assert not outcome.is_error
        denied = await client.call_tool(
            "tool_list_sheets",
            {},
            meta={
                "authorization": signed_token(
                    str(owned_ledger.other_owner), APPLICATION_SECRET
                )
            },
            raise_on_error=False,
        )
    assert denied.is_error


async def test_network_service_can_forward_a_signed_application_identity(
    owned_ledger, tmp_path, network_settings
):
    """Transport authentication and delegated user identity remain distinct."""
    async with network_server(
        owned_ledger, tmp_path / "network.log", network_settings
    ) as url:
        async with Client(url, auth=signed_token("service")) as client:
            outcome = await client.call_tool(
                "tool_list_sheets",
                {},
                meta={
                    "authorization": signed_token(
                        str(owned_ledger.owner), APPLICATION_SECRET
                    )
                },
            )
    assert not outcome.is_error


async def test_stdio_without_owner_identity_is_denied(owned_ledger):
    async with stdio_client(owned_ledger, {"MCP_USER_ID": ""}) as client:
        result = await client.call_tool("tool_list_sheets", {}, raise_on_error=False)
    assert result.is_error and "Owner identity required" in str(result.content)


async def test_http_protocol_revisions_keep_the_read_contract(
    owned_ledger, tmp_path, network_settings
):
    async with network_server(
        owned_ledger, tmp_path / "protocol.log", network_settings
    ) as url:
        for mode, version in [("auto", "2026-07-28"), ("legacy", "2025-11-25")]:
            async with Client(
                url, auth=signed_token(str(owned_ledger.owner)), mode=mode
            ) as client:
                assert client.protocol_version == version
                assert {tool.name for tool in await client.list_tools()} == READ_TOOLS
                data = await call_json(
                    client, "tool_get_sheet_data", {"sheet": "Claims", "range": "A1:B2"}
                )
                assert data["values"] == [["Name", "Amount"], ["A", 10]]


def http_read_request() -> dict:
    """Build a current-protocol read with matching per-request metadata."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }


def http_headers(owner: int) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {signed_token(str(owner))}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/list",
    }


async def test_current_http_reads_use_stateless_metadata(owned_ledger, http_endpoint):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            http_endpoint,
            headers=http_headers(owned_ledger.owner),
            json=http_read_request(),
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "mcp-session-id" not in response.headers
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == READ_TOOLS


async def test_handshake_http_supports_json_and_sse_responses(
    owned_ledger, http_endpoint, network_settings
):
    headers = {**http_headers(owned_ledger.owner), "MCP-Protocol-Version": "2025-11-25"}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            http_endpoint,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    assert response.status_code == 200
    expected_type = (
        "application/json"
        if network_settings["FASTMCP_JSON_RESPONSE"] == "true"
        else "text/event-stream"
    )
    assert response.headers["content-type"].startswith(expected_type)
    payload = (
        response.json()
        if expected_type == "application/json"
        else json.loads(
            next(
                line[6:]
                for line in response.text.splitlines()
                if line.startswith("data: ")
            )
        )
    )
    assert {tool["name"] for tool in payload["result"]["tools"]} == READ_TOOLS


@pytest.mark.parametrize("credential", ["missing", "expired", "forged"])
async def test_http_rejects_invalid_bearer_credentials(
    owned_ledger, http_endpoint, credential
):
    headers = http_headers(owned_ledger.owner)
    if credential == "missing":
        headers.pop("Authorization")
    elif credential == "expired":
        headers["Authorization"] = (
            f"Bearer {signed_token(str(owned_ledger.owner), exp=0)}"
        )
    else:
        headers["Authorization"] = (
            f"Bearer {signed_token(str(owned_ledger.owner), 'wrong-verification-key-with-at-least-32-bytes')}"
        )
    async with httpx.AsyncClient() as client:
        response = await client.post(
            http_endpoint, headers=headers, json=http_read_request()
        )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "origin_case",
    [("https://klaudia.example", 200), ("https://untrusted.example", 403)],
)
async def test_http_checks_configured_browser_origins(
    owned_ledger, http_endpoint, origin_case
):
    origin, status = origin_case
    headers = {
        **http_headers(owned_ledger.owner),
        "Host": "ledger.example",
        "Origin": origin,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            http_endpoint, headers=headers, json=http_read_request()
        )
    assert response.status_code == status


async def test_http_rejects_mismatched_routing_headers(owned_ledger, http_endpoint):
    headers = {**http_headers(owned_ledger.owner), "Mcp-Method": "tools/call"}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            http_endpoint, headers=headers, json=http_read_request()
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_current_http_rejects_legacy_session_methods(
    owned_ledger, http_endpoint, method
):
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method, http_endpoint, headers=http_headers(owned_ledger.owner)
        )
    assert response.status_code == 405


@pytest.mark.parametrize("failure", ["unauthorized", "missing-endpoint"])
async def test_registry_propagates_real_http_connection_failures(
    owned_ledger, http_endpoint, failure
):
    url = f"{http_endpoint}/missing" if failure == "missing-endpoint" else http_endpoint
    token = (
        signed_token(str(owned_ledger.owner), exp=0)
        if failure == "unauthorized"
        else signed_token(str(owned_ledger.owner))
    )
    registry = MCPToolRegistry("ledger", url=url, auth_token=token)
    with pytest.raises(RuntimeError, match="connection failed"):
        await registry.connect()


async def test_http_revoked_ownership_is_checked_on_each_call(
    owned_ledger, tmp_path, network_settings
):
    async with network_server(
        owned_ledger, tmp_path / "revoked.log", network_settings
    ) as url:
        async with Client(url, auth=signed_token(str(owned_ledger.owner))) as client:
            await client.call_tool("tool_list_sheets", {})
            await owned_ledger.store.pool.execute(
                "UPDATE ledger_spreadsheet SET user_id = $1 WHERE spreadsheet_id = $2",
                owned_ledger.other_owner,
                owned_ledger.workbook,
            )
            denied = await client.call_tool(
                "tool_list_sheets", {}, raise_on_error=False
            )
    assert denied.is_error and "Workbook not found" in str(denied.content)


async def test_http_rejects_all_mutations_and_invalid_delegated_identity(
    owned_ledger, tmp_path, network_settings
):
    names = [
        "tool_update_cells",
        "tool_batch_update_cells",
        "tool_append_rows",
        "tool_add_rows",
        "tool_add_columns",
        "tool_clear_range",
        "tool_create_sheet",
        "tool_rename_sheet",
        "tool_copy_sheet",
        "tool_delete_sheet",
        "tool_batch_update",
        "tool_register_table",
        "tool_update_table",
        "tool_append_rows_checked",
    ]
    before = await owned_ledger.store.get_snapshot(owned_ledger.workbook, "Claims")
    async with network_server(
        owned_ledger, tmp_path / "denied.log", network_settings
    ) as url:
        async with Client(url, auth=signed_token(str(owned_ledger.owner))) as client:
            for name in names:
                result = await client.call_tool(name, {}, raise_on_error=False)
                assert result.is_error and "MCP tool is not available" in str(
                    result.content
                )
            for token in [
                signed_token(str(owned_ledger.owner), APPLICATION_SECRET, exp=0),
                signed_token(
                    str(owned_ledger.owner),
                    "forged-application-key-with-at-least-32-bytes",
                ),
            ]:
                result = await client.call_tool(
                    "tool_list_sheets",
                    {},
                    meta={"authorization": token},
                    raise_on_error=False,
                )
                assert result.is_error and "Owner identity required" in str(
                    result.content
                )
    after = await owned_ledger.store.get_snapshot(owned_ledger.workbook, "Claims")
    assert after == before
