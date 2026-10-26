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
import jwt
import pytest

from ledger.catalogue import CatalogueStore
from ledger.resources import TableRegistration
from ledger.store import LedgerStore
from tests.integration.postgres import POSTGRES_TEST_URL

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
async def stdio_client(fixture: OwnedLedger):
    """Run the actual stdio server for the configured local owner."""
    transport = StdioTransport(
        command=sys.executable,
        args=["main.py", "--transport", "stdio"],
        cwd=str(SERVER_DIR),
        env=server_environment(fixture),
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
async def network_server(fixture: OwnedLedger, log_path: Path, transport: str):
    """Run HTTP or SSE with an intentionally set local owner."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **server_environment(fixture),
        "FASTMCP_HOST": "127.0.0.1",
        "FASTMCP_PORT": str(port),
    }
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "main.py",
            "--transport",
            transport,
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
            suffix = "mcp" if transport == "http" else "sse"
            yield f"http://127.0.0.1:{port}/{suffix}"
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


@pytest.fixture(params=["http", "sse"])
def network_transport(request):
    """Exercise both externally reachable transports."""
    return request.param


async def test_network_tokens_bind_workbook_ownership(
    owned_ledger, tmp_path, network_transport
):
    """Verified HTTP subjects can read only their currently owned workbooks."""
    async with network_server(
        owned_ledger, tmp_path / "network.log", network_transport
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
    owned_ledger, tmp_path, network_transport
):
    """A process owner setting never authorizes an HTTP service credential."""
    async with network_server(
        owned_ledger, tmp_path / "network.log", network_transport
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
    owned_ledger, tmp_path, network_transport
):
    """Transport authentication and delegated user identity remain distinct."""
    async with network_server(
        owned_ledger, tmp_path / "network.log", network_transport
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
