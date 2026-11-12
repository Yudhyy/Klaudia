"""Application discovery fails explicitly when metadata exceeds its read budget."""

from unittest.mock import AsyncMock

import pytest

from klaudia_api.adapters.ledger.catalogue.service import CatalogueService
from ledger.resources import ResourceInspection, ResourceSearch


async def test_oversized_search_evidence_is_rejected():
    """A large metadata response cannot silently overflow model context."""
    store = AsyncMock()
    store.search_owned.return_value = {"candidates": [{"description": "x" * 65536}]}
    with pytest.raises(ValueError, match="Evidence exceeds"):
        await CatalogueService(store).search(12, ResourceSearch(intent="claims"))
    store.search_owned.assert_awaited_once_with(12, ResourceSearch(intent="claims"))


async def test_inspection_pages_before_enforcing_byte_budget():
    """A wide schema remains readable through a page that fits the budget."""
    store = AsyncMock()
    columns = [{"name": f"{index}" + "x" * 250} for index in range(256)]
    store.inspect_owned.return_value = {"table_id": "tbl_wide", "columns": columns}
    page = await CatalogueService(store).inspect(
        12, ResourceInspection(table_id="tbl_wide")
    )
    assert len(page["columns"]) == 32
    assert page["column_count"] == 256
    assert page["has_more_columns"] is True
    assert len(store.inspect_owned.return_value["columns"]) == 256
