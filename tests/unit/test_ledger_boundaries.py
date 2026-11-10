"""Saved workbook evidence uses a supplied owner-scoped read capability."""

import json
from unittest.mock import AsyncMock

import pytest

from app.adapters.agent.checkpoints import SavedSources, check_saved_access
from app.services.documents.ports import DocumentArchive
from app.services.memory.ports import DocumentReader
from ledger_core.resources import ResourceNotFoundError


@pytest.fixture
def sources():
    """Supply source interfaces without a raw query capability."""
    from app.services.catalogue.ports import WorkbookAccess

    memory = AsyncMock(spec=DocumentReader)
    documents = AsyncMock(spec=DocumentArchive)
    documents.count_owned_documents.return_value = 0
    workbooks = AsyncMock(spec=WorkbookAccess)
    workbooks.count_owned_workbooks.return_value = 1
    return SavedSources(memory, documents, workbooks)


def saved_workbook(evidence: dict, tool_name: str) -> dict:
    """Build original input with duplicate references to the same saved source.

    Args:
        evidence: Saved tool evidence.
        tool_name: Tool that produced the evidence.

    Returns:
        Task identity and the original serialized evidence format.
    """
    entry = [tool_name, {}, json.dumps(evidence)]
    return {
        "user_id": 42,
        "input_payload": json.dumps({"turn": {"document_ids": []}}),
        "checkpoint": json.dumps({"evidence": [entry, entry]}),
    }


@pytest.mark.parametrize(
    "tool_name,evidence",
    [
        ("list_authoring_sheets", {"sheets": [{"spreadsheet_id": "book"}]}),
        ("inspect_sheet_region", {"spreadsheet_id": "book"}),
        ("inspect_typed_workbook", {"workbook_id": "book"}),
        ("search_resources", {"candidates": [{"spreadsheet_id": "book"}]}),
        ("inspect_resource", {"spreadsheet_id": "book"}),
        ("financial_query", {"sources": [{"spreadsheet_id": "book"}]}),
        (
            "execute_operation",
            {"status": "committed", "target": {"spreadsheet_id": "book"}},
        ),
    ],
)
async def test_replay_checks_each_saved_workbook_family(sources, tool_name, evidence):
    """Recheck current ownership once per workbook, without receiving SQL access."""
    await check_saved_access(saved_workbook(evidence, tool_name), sources)
    sources.workbooks.count_owned_workbooks.assert_awaited_once_with(42, ["book"])
    sources.documents.count_owned_documents.assert_awaited_once_with(42, [])


async def test_replay_rejects_revoked_workbook_access(sources):
    """A saved tool result cannot restore lost workbook ownership."""
    sources.workbooks.count_owned_workbooks.return_value = 0
    with pytest.raises(ResourceNotFoundError, match="Saved task source"):
        await check_saved_access(
            saved_workbook({"spreadsheet_id": "book"}, "inspect_resource"), sources
        )


async def test_replay_does_not_hide_an_unavailable_workbook_reader(sources):
    """A workbook read failure stops replay before another source read."""
    sources.workbooks.count_owned_workbooks.side_effect = ConnectionError("ledger read")
    with pytest.raises(ConnectionError, match="ledger read"):
        await check_saved_access(
            saved_workbook({"spreadsheet_id": "book"}, "inspect_resource"), sources
        )
    sources.documents.count_owned_documents.assert_not_awaited()


async def test_workbook_reader_reuses_the_supplied_connection():
    """Do not allocate another pool slot for task-bound ownership reads."""
    from ledger.catalogue import CatalogueStore
    from ledger.connections import BorrowedConnection

    connection = AsyncMock()
    connection.fetchval.return_value = 1
    reader = CatalogueStore(BorrowedConnection(connection))
    assert await reader.count_owned_workbooks(42, ["book"]) == 1
    assert connection.fetchval.await_args.args[1:] == (42, ["book"])
    connection.acquire.assert_not_called()
    connection.close.assert_not_awaited()
