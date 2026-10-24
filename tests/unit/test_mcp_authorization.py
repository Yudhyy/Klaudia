"""Reject unscoped reads and legacy writes at the MCP request boundary."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastmcp import Client
import pytest

from ledger.server import mcp


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
WRITE_CALLS = [
    ("tool_update_cells", {"sheet": "Claims", "range": "A1", "data": [[1]]}),
    ("tool_batch_update_cells", {"sheet": "Claims", "updates": []}),
    ("tool_append_rows", {"sheet": "Claims", "data": [[1]]}),
    ("tool_add_rows", {"sheet": "Claims", "count": 1}),
    ("tool_add_columns", {"sheet": "Claims", "count": 1}),
    ("tool_clear_range", {"sheet": "Claims", "range": "A1"}),
    ("tool_create_sheet", {"title": "Claims"}),
    ("tool_rename_sheet", {"sheet": "Claims", "new_name": "Other"}),
    ("tool_copy_sheet", {"src_sheet": "Claims", "dst_sheet": "Other"}),
    ("tool_delete_sheet", {"sheet": "Claims"}),
    ("tool_batch_update", {"requests": [{"deleteSheet": {"sheetId": 1}}]}),
    ("tool_register_table", {"definition": {}}),
    ("tool_update_table", {"change": {}}),
    (
        "tool_append_rows_checked",
        {
            "operation": {
                "sheet_id": 1,
                "expected_revision": 0,
                "idempotency_key": "stale",
                "rows": [[1]],
            }
        },
    ),
]


@pytest.fixture
def unused_store(monkeypatch):
    """Fail if a rejected request reaches storage.

    Args:
        monkeypatch: Pytest patch scope.

    Returns:
        Mock store with forbidden database access.
    """
    store = SimpleNamespace(
        pool=SimpleNamespace(
            acquire=AsyncMock(
                side_effect=AssertionError("Rejected call reached storage")
            )
        )
    )

    @asynccontextmanager
    async def lifespan(server):
        """Expose the mock store without opening application services."""
        yield store

    monkeypatch.setattr(mcp, "_lifespan", lifespan)
    monkeypatch.delenv("MCP_USER_ID", raising=False)
    return store


async def test_ordinary_mcp_surface_is_read_only(unused_store):
    """Tool discovery omits every mutation, including checked appends."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert {tool.name for tool in tools} == READ_TOOLS


@pytest.mark.parametrize("name,arguments", WRITE_CALLS)
async def test_direct_mutation_calls_are_denied(unused_store, name, arguments):
    """Calling a hidden mutation by name cannot reach its implementation.

    Args:
        unused_store: Storage that must stay unused.
        name: Mutation tool name.
        arguments: Request arguments, including destructive targets.
    """
    async with Client(mcp) as client:
        outcome = await client.call_tool(name, arguments, raise_on_error=False)
    assert outcome.is_error and "MCP tool is not available" in str(outcome.content)


async def test_read_without_owner_identity_is_denied(unused_store):
    """A supplied workbook ID cannot grant read authority."""
    async with Client(mcp) as client:
        outcome = await client.call_tool(
            "tool_list_sheets", {"spreadsheet_id": "foreign"}, raise_on_error=False
        )
    assert outcome.is_error and "Owner identity required" in str(outcome.content)
